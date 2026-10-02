#!/usr/bin/env python3
"""Container-side setup and scanning. Uses only the Python standard library."""

from __future__ import annotations

import argparse
import base64
from functools import lru_cache
import hashlib
import json
import math
import os
from pathlib import Path
import re
import secrets
import signal
import subprocess
import sys
import tempfile
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import uuid


class PackageError(Exception):
    pass


class APIError(PackageError):
    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


def atomic_write(path, content):
    path = Path(path)
    if path.is_symlink():
        raise PackageError("State files must not be symbolic links: " + path.name)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary = tempfile.mkstemp(prefix=".pending-", dir=str(path.parent))
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, str(path))
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class State:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.path = self.directory / "credentials.json"
        self.data = None
        if self.path.is_symlink():
            raise PackageError("credentials.json must not be a symbolic link.")
        if self.path.exists():
            try:
                self.data = json.loads(self.path.read_text(encoding="utf-8"))
            except (ValueError, OSError) as error:
                raise PackageError("Cannot read credentials.json; restore matching state.") from error
            if not isinstance(self.data, dict) or self.data.get("schema_version") != 1:
                raise PackageError("Unsupported credentials.json format; restore matching state.")
            for field in ("database_password", "admin_password"):
                if not isinstance(self.data.get(field), str) or not self.data[field]:
                    raise PackageError("Incomplete credentials.json; restore matching state.")
            if not re.fullmatch(r"[A-Za-z0-9_-]+", self.data["database_password"]):
                raise PackageError("Invalid saved database credentials; restore matching state.")
            if not isinstance(self.data.get("projects"), dict):
                raise PackageError("Invalid saved project credentials; restore matching state.")
            if not isinstance(self.data.get("admin_initialized"), bool):
                raise PackageError("Invalid administrator setup state; restore matching state.")
            for record in self.data["projects"].values():
                if not isinstance(record, dict) or any(
                    not isinstance(record.get(field), str) for field in ("key", "token_name", "token")
                ):
                    raise PackageError("Invalid saved project credentials; restore matching state.")

    def initialize(self):
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(str(self.directory), 0o700)
        if self.data is None:
            self.data = {
                "schema_version": 1,
                "database_password": secrets.token_urlsafe(32),
                "admin_password": "Aa1!" + secrets.token_urlsafe(32),
                "admin_initialized": False,
                "projects": {},
            }
            # Save the intended password BEFORE applying it on the server.
            self.save()
        else:
            os.chmod(str(self.path), 0o600)
        atomic_write(
            self.directory / "runtime.env",
            "SONAR_DB_PASSWORD=" + self.data["database_password"] + "\n",
        )
        (self.directory / "cache").mkdir(exist_ok=True, mode=0o700)

    def require(self):
        if self.data is None:
            raise PackageError("The package is not initialized. Run ./sonar up first.")
        return self.data

    def save(self):
        atomic_write(self.path, json.dumps(self.require(), indent=2) + "\n")

    def redact(self, message):
        if self.data:
            values = [self.data["admin_password"], self.data["database_password"]]
            values.extend(
                record.get("token", "") for record in self.data["projects"].values()
                if isinstance(record, dict)
            )
            for value in values:
                if value:
                    message = message.replace(value, "[redacted]")
        return message


class SonarClient:
    def __init__(self, base_url, password=None, token=None, timeout=10):
        self.base_url = base_url.rstrip("/")
        self.password = password
        self.token = token
        self.timeout = timeout

    def request(self, path, parameters=None, post=False, sensitive=False):
        headers = {"Accept": "application/json"}
        if self.token:
            headers["Authorization"] = "Bearer " + self.token
        elif self.password:
            basic = base64.b64encode(("admin:" + self.password).encode()).decode()
            headers["Authorization"] = "Basic " + basic
        url = self.base_url + "/" + path.lstrip("/")
        encoded = urlencode(parameters or {}).encode("utf-8")
        if post:
            headers["Content-Type"] = "application/x-www-form-urlencoded"
            body = encoded
        else:
            body = None
            if encoded:
                url += "?" + encoded.decode("utf-8")
        request = Request(url, data=body, headers=headers, method="POST" if post else "GET")
        try:
            with urlopen(request, timeout=self.timeout) as response:
                content = response.read()
        except HTTPError as error:
            detail = ""
            if not sensitive:
                try:
                    payload = json.loads(error.read())
                    detail = ": " + "; ".join(item["msg"] for item in payload.get("errors", []))
                except (ValueError, KeyError, TypeError):
                    pass
            raise APIError("SonarQube " + path + " returned HTTP " + str(error.code) + detail,
                           error.code) from error
        except (URLError, TimeoutError, OSError) as error:
            raise APIError("Cannot reach SonarQube at " + self.base_url) from error
        if not content:
            return {}
        try:
            return json.loads(content)
        except ValueError as error:
            raise APIError("SonarQube returned an invalid JSON response for " + path) from error

    def authenticated(self):
        try:
            return self.request("api/authentication/validate", sensitive=True).get("valid") is True
        except APIError as error:
            if error.status == 401:
                return False
            raise


def positive_timeout(name, default):
    try:
        value = float(os.environ.get(name, default))
    except ValueError as error:
        raise PackageError(name + " must be a positive number of seconds.") from error
    if not math.isfinite(value) or value <= 0:
        raise PackageError(name + " must be a positive number of seconds.")
    return value


def _unescape(value):
    result = []
    index = 0
    escapes = {"t": "\t", "r": "\r", "n": "\n", "f": "\f"}
    while index < len(value):
        character = value[index]
        if character != "\\":
            result.append(character)
            index += 1
            continue
        index += 1
        if index == len(value):
            raise PackageError("Invalid trailing backslash in project properties.")
        character = value[index]
        if character == "u":
            hexadecimal = value[index + 1:index + 5]
            if len(hexadecimal) != 4 or not re.fullmatch(r"[0-9a-fA-F]{4}", hexadecimal):
                raise PackageError("Invalid Unicode escape in project properties.")
            result.append(chr(int(hexadecimal, 16)))
            index += 5
        else:
            result.append(escapes.get(character, character))
            index += 1
    return "".join(result)


def read_properties(path):
    """Read UTF-8 Java properties, including continuations and escaped separators."""
    properties = {}
    pending = ""
    for physical in Path(path).read_text(encoding="utf-8-sig").splitlines():
        line = pending + (physical.lstrip(" \t\f") if pending else physical)
        trailing = len(line) - len(line.rstrip("\\"))
        if trailing % 2:
            pending = line[:-1]
            continue
        pending = ""
        line = line.lstrip(" \t\f")
        if not line or line[0] in "#!":
            continue
        index = 0
        while index < len(line):
            if line[index] == "\\":
                index += 2
            elif line[index] in "=: \t\f":
                break
            else:
                index += 1
        key = _unescape(line[:index])
        rest = line[index:].lstrip(" \t\f")
        if rest.startswith(("=", ":")):
            rest = rest[1:].lstrip(" \t\f")
        properties[key] = _unescape(rest)
    if pending:
        raise PackageError("Unfinished continuation in " + Path(path).name)
    return properties


def write_properties(properties):
    def escape(value):
        result = []
        for index, character in enumerate(value):
            if character == "\\":
                result.append("\\\\")
            elif character in "\n\r\t\f":
                result.append({"\n": "\\n", "\r": "\\r", "\t": "\\t", "\f": "\\f"}[character])
            elif character in "=:# !" and (index == 0 or character in "=:"):
                result.append("\\" + character)
            else:
                result.append(character)
        return "".join(result)
    return "".join(escape(key) + "=" + escape(value) + "\n" for key, value in properties.items())


@lru_cache(maxsize=256)
def pattern_expression(pattern):
    result = []
    index = 0
    while index < len(pattern):
        if pattern[index:index + 3] == "**/":
            result.append("(?:.*/)?")
            index += 3
        elif pattern[index:index + 2] == "**":
            result.append(".*")
            index += 2
        elif pattern[index] == "*":
            result.append("[^/]*")
            index += 1
        elif pattern[index] == "?":
            result.append("[^/]")
            index += 1
        else:
            result.append(re.escape(pattern[index]))
            index += 1
    return re.compile("^" + "".join(result) + "$")


def matches(path, pattern):
    return pattern_expression(pattern).fullmatch(path) is not None


def comma_values(value):
    return [part.strip() for part in value.split(",") if part.strip()]


def in_directory(path, directory):
    try:
        path.relative_to(directory)
        return True
    except ValueError:
        return False


CONTROLLED_PROPERTIES = {
    "sonar.projectKey", "sonar.projectName", "sonar.projectBaseDir", "sonar.working.directory",
    "sonar.host.url", "sonar.token", "sonar.login", "sonar.password", "sonar.userHome",
    "sonar.verbose", "sonar.log.level",
}


class SonarPackage:
    def __init__(self, state_dir=None, projects_dir=None, config_dir=None,
                 api_url="http://sonarqube:9000"):
        self.state = State(state_dir or os.environ.get("SONAR_STATE_DIR", "/state"))
        self.projects = Path(projects_dir or os.environ.get("SONAR_PROJECTS_DIR", "/projects"))
        self.config = Path(config_dir or os.environ.get("SONAR_CONFIG_DIR", "/config"))
        self.api_url = api_url
        try:
            port = int(os.environ.get("SONAR_PORT", "9000"))
        except ValueError as error:
            raise PackageError("SONAR_PORT must be an integer between 1 and 65535.") from error
        if not 1 <= port <= 65535:
            raise PackageError("SONAR_PORT must be between 1 and 65535.")
        self.web_url = "http://localhost:" + str(port)
        self.startup_timeout = positive_timeout("SONAR_STARTUP_TIMEOUT", "300")
        self.analysis_timeout = positive_timeout("SONAR_ANALYSIS_TIMEOUT", "300")
        self.scan_timeout = positive_timeout("SONAR_SCAN_TIMEOUT", "1800")

    def client(self, token=None):
        password = None if token else self.state.require()["admin_password"]
        return SonarClient(self.api_url, password=password, token=token,
                           timeout=min(10, self.analysis_timeout))

    def preflight(self, sysctl_dir=Path("/proc/sys")):
        for name, relative, minimum in (("vm.max_map_count", "vm/max_map_count", 524288),
                                        ("fs.file-max", "fs/file-max", 131072)):
            try:
                value = int((Path(sysctl_dir) / relative).read_text().strip())
            except (ValueError, OSError) as error:
                raise PackageError("Cannot check Docker host prerequisite " + name + ". See README.md.") from error
            if value < minimum:
                raise PackageError("Docker host " + name + "=" + str(value) + " is below " + str(minimum)
                                   + ". Set it on the Linux Docker host or Docker Desktop's Linux VM; "
                                   "see README.md. No host settings were changed.")

    def wait_ready(self):
        deadline = time.monotonic() + self.startup_timeout
        client = SonarClient(self.api_url, timeout=min(10, self.startup_timeout))
        last_status = None
        next_notice = 0
        while time.monotonic() < deadline:
            try:
                status = client.request("api/system/status").get("status", "UNKNOWN")
            except APIError as error:
                if error.status and error.status < 500:
                    raise
                status = "STARTING"
            if status == "UP":
                return
            if status in ("DB_MIGRATION_NEEDED", "DB_MIGRATION_RUNNING"):
                raise PackageError("SonarQube needs database migration. Check service logs and the pinned version.")
            now = time.monotonic()
            if status != last_status or now >= next_notice:
                print("Waiting for SonarQube (" + status + ")...", flush=True)
                next_notice = now + 30
                last_status = status
            time.sleep(min(2, max(0, deadline - now)))
        raise PackageError("SonarQube startup timed out. Run ./sonar logs; check Docker resources, "
                           "Linux Elasticsearch limits, and matching database credentials.")

    def bootstrap(self):
        data = self.state.require()
        self.wait_ready()
        client = self.client()
        if not client.authenticated():
            if data.get("admin_initialized"):
                raise PackageError("Saved administrator credentials no longer match SonarQube. "
                                   "Restore matching .state or update the saved administrator password.")
            initial = SonarClient(self.api_url, password="admin")
            if not initial.authenticated():
                raise PackageError("SonarQube accepts neither the saved nor initial administrator credentials. "
                                   "Restore matching .state; reset only if you intend to delete history.")
            initial.request("api/users/change_password", {
                "login": "admin", "previousPassword": "admin", "password": data["admin_password"],
            }, post=True, sensitive=True)
            if not client.authenticated():
                raise PackageError("Administrator password update could not be verified. Retry ./sonar up.")
        if not data.get("admin_initialized"):
            data["admin_initialized"] = True
            self.state.save()
        for key, value in (("sonar.core.serverBaseURL", self.web_url),
                           ("sonar.forceAuthentication", "true")):
            client.request("api/settings/set", {"key": key, "value": value}, post=True)
        print("SonarQube is ready at " + self.web_url + ".", flush=True)
        print("Use ./sonar credentials to retrieve the web login.", flush=True)

    def names(self, name=None, all_projects=False):
        if all_projects:
            names = sorted(path.name for path in self.projects.iterdir()
                           if path.is_dir() and not path.name.startswith("."))
            if not names:
                raise PackageError("No repositories found. Clone PHP code into projects/<name>/ first.")
        elif name:
            names = [name]
        else:
            raise PackageError("Specify a repository name or --all.")
        if name and all_projects:
            raise PackageError("Choose a repository name or --all, not both.")
        actual_names = {path.name for path in self.projects.iterdir()}
        for current in names:
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,199}", current):
                raise PackageError("Repository names must use letters, digits, dots, underscores, or dashes "
                                   "and start with a letter or digit (maximum 200 characters).")
            repository = self.projects / current
            if current not in actual_names or repository.is_symlink() or not repository.is_dir():
                raise PackageError("Repository not found, has different capitalization, or is a symbolic link: projects/" + current)
        return names

    def repository_path(self, repository, value, label):
        if not value or Path(value).is_absolute():
            raise PackageError(label + " must use a nonempty path relative to the repository.")
        path = (repository / value).resolve()
        if not in_directory(path, repository.resolve()):
            raise PackageError(label + " points outside the selected repository: " + value)
        if not path.exists():
            raise PackageError(label + " path does not exist: " + value)
        return path

    def properties(self, name):
        repository = self.projects / name
        properties = read_properties(self.config / "defaults.properties")
        override = self.config / "projects" / (name + ".properties")
        if override.is_symlink():
            raise PackageError("Project configuration must not be a symbolic link: " + override.name)
        if override.exists():
            additions = read_properties(override)
            for key in additions:
                if not key.startswith("sonar."):
                    raise PackageError("Project configuration accepts only sonar.* properties: " + key)
                if (key in CONTROLLED_PROPERTIES or key.startswith((
                        "sonar.branch.", "sonar.pullrequest.", "sonar.scanner."))):
                    raise PackageError("The package controls this property: " + key)
            properties.update(additions)
        source_paths = comma_values(properties.get("sonar.sources", "."))
        if not source_paths:
            raise PackageError("sonar.sources must select at least one path.")
        sources = [self.repository_path(repository, value, "sonar.sources") for value in source_paths]
        test_paths = comma_values(properties.get("sonar.tests", ""))
        tests = [self.repository_path(repository, value, "sonar.tests") for value in test_paths]
        test_patterns = comma_values(properties.get("sonar.test.inclusions", ""))
        if tests:
            exclusions = comma_values(properties.get("sonar.exclusions", ""))
            if test_patterns:
                exclusions.extend(test_patterns)
            else:
                for path in tests:
                    if path == repository.resolve():
                        raise PackageError("sonar.tests=. requires sonar.test.inclusions to separate test files.")
                    relative = path.relative_to(repository.resolve()).as_posix()
                    exclusions.append(relative + "/**" if path.is_dir() else relative)
            properties["sonar.exclusions"] = ",".join(dict.fromkeys(exclusions))
        for key in ("sonar.php.coverage.reportPaths", "sonar.php.tests.reportPath",
                    "sonar.coverageReportPaths", "sonar.testExecutionReportPaths"):
            if key not in properties:
                continue
            reports = comma_values(properties[key])
            if not reports or (key == "sonar.php.tests.reportPath" and len(reports) != 1):
                raise PackageError("Invalid report path configuration: " + key)
            for value in reports:
                if not self.repository_path(repository, value, key).is_file():
                    raise PackageError(key + " must point to a report file: " + value)
        inclusions = comma_values(properties.get("sonar.inclusions", ""))
        exclusions = comma_values(properties.get("sonar.exclusions", ""))
        suffixes = comma_values(properties.get("sonar.php.file.suffixes", ".php,.php3,.php4,.php5,.phtml,.inc"))
        found = False
        for source in sources:
            paths = source.rglob("*") if source.is_dir() else [source]
            for path in paths:
                if path.is_symlink() or not path.is_file():
                    continue
                if path.suffix.lower() not in suffixes and path.name != "php.ini":
                    continue
                relative = path.relative_to(repository.resolve()).as_posix()
                if inclusions and not any(matches(relative, pattern) for pattern in inclusions):
                    continue
                if any(matches(relative, pattern) for pattern in exclusions):
                    continue
                found = True
                break
            if found:
                break
        if not found:
            raise PackageError("No PHP source files selected in projects/" + name + ". Check source paths and exclusions.")
        return properties

    def validate(self, names):
        for name in names:
            self.properties(name)

    def ensure_project(self, name):
        client = self.client()
        key = "local:" + name
        existing = client.request("api/projects/search", {"projects": key}).get("components", [])
        if not existing:
            client.request("api/projects/create", {
                "name": name, "project": key, "visibility": "private",
            }, post=True)
        elif existing[0].get("visibility") == "public":
            client.request("api/projects/update_visibility", {"project": key, "visibility": "private"}, post=True)
        record = self.state.require()["projects"].get(name, {})
        if not isinstance(record, dict):
            raise PackageError("Invalid saved project credentials for " + name)
        token = record.get("token")
        if token and self.client(token=token).authenticated():
            return key, token
        token_name = "sonar-package-" + name[:32] + "-" + hashlib.sha256(key.encode()).hexdigest()[:16] + "-analysis"
        # A previous run can stop after token creation but before saving its value.
        # Revoke only the package's deterministic token name before recreating it.
        tokens = client.request("api/user_tokens/search").get("userTokens", [])
        if any(item.get("name") == token_name for item in tokens):
            client.request("api/user_tokens/revoke", {"name": token_name}, post=True, sensitive=True)
        response = client.request("api/user_tokens/generate", {
            "name": token_name, "type": "PROJECT_ANALYSIS_TOKEN", "projectKey": key,
        }, post=True, sensitive=True)
        token = response.get("token")
        if not isinstance(token, str) or not token:
            raise PackageError("SonarQube did not return a project analysis token.")
        self.state.require()["projects"][name] = {"key": key, "token_name": token_name, "token": token}
        self.state.save()
        return key, token

    def run_scanner(self, repository, settings, token):
        environment = os.environ.copy()
        environment.update({
            "SONAR_TOKEN": token,
            "SONAR_HOST_URL": self.api_url,
            "SONAR_USER_HOME": str(self.state.directory / "cache"),
            "SONAR_SCANNER_JAVA_OPTS": "-Xmx1g",
        })
        command = ["sonar-scanner", "-Dproject.settings=" + str(settings),
                   "-Dsonar.projectBaseDir=" + str(repository), "-Dsonar.verbose=false"]
        process = subprocess.Popen(command, cwd=str(repository), env=environment,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   text=True, encoding="utf-8", errors="replace", start_new_session=True)

        def relay():
            for line in process.stdout:
                print(self.state.redact(line.rstrip("\n")), flush=True)

        output = threading.Thread(target=relay, daemon=True)
        output.start()
        try:
            return_code = process.wait(timeout=self.scan_timeout)
        except (subprocess.TimeoutExpired, KeyboardInterrupt) as error:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
            if isinstance(error, KeyboardInterrupt):
                raise
            raise PackageError("Scanner timed out. Adjust SONAR_SCAN_TIMEOUT for a larger repository.") from error
        finally:
            output.join(timeout=5)
            process.stdout.close()
        if return_code:
            raise PackageError("Scanner failed with exit status " + str(return_code) + ". See the scanner output above.")

    def wait_analysis(self, task_id):
        client = self.client()
        deadline = time.monotonic() + self.analysis_timeout
        while time.monotonic() < deadline:
            try:
                task = client.request("api/ce/task", {"id": task_id}).get("task", {})
                status = task.get("status")
                if status in ("FAILED", "CANCELED"):
                    detail = self.state.redact(str(task.get("errorMessage", "Check ./sonar logs.")))
                    raise PackageError("SonarQube analysis " + status.lower() + ": " + detail)
                if status == "SUCCESS":
                    analysis_id = task.get("analysisId")
                    if not analysis_id:
                        raise PackageError("SonarQube completed the task without an analysis ID.")
                    gate = client.request("api/qualitygates/project_status", {"analysisId": analysis_id})
                    gate_status = gate.get("projectStatus", {}).get("status")
                    if gate_status not in ("OK", "ERROR", "WARN", "NONE"):
                        raise PackageError("SonarQube returned an unknown quality gate status.")
                    return gate_status
                if status not in ("PENDING", "IN_PROGRESS"):
                    raise PackageError("SonarQube returned an unknown analysis task status.")
            except APIError as error:
                if error.status and error.status < 500:
                    raise
            time.sleep(min(2, max(0, deadline - time.monotonic())))
        raise PackageError("Analysis processing timed out. Results may appear later in the dashboard; "
                           "check ./sonar logs or increase SONAR_ANALYSIS_TIMEOUT.")

    def scan_one(self, name):
        properties = self.properties(name)
        key, token = self.ensure_project(name)
        scan_dir = self.state.directory / "scans" / name / uuid.uuid4().hex
        scan_dir.mkdir(parents=True, mode=0o700)
        work = scan_dir / "work"
        properties.update({
            "sonar.projectKey": key,
            "sonar.projectName": name,
            "sonar.working.directory": str(work),
            "sonar.scanner.connectTimeout": "10",
            "sonar.scanner.responseTimeout": "60",
        })
        settings = scan_dir / "project.properties"
        atomic_write(settings, write_properties(properties))
        print("\nScanning " + name + " (" + key + ")...", flush=True)
        self.run_scanner(self.projects / name, settings, token)
        report = work / "report-task.txt"
        if not report.is_file():
            raise PackageError("Scanner did not produce a task report; analysis completion cannot be verified.")
        task_id = read_properties(report).get("ceTaskId")
        if not task_id:
            raise PackageError("Scanner task report has no task ID.")
        print("Waiting for server analysis processing...", flush=True)
        gate = self.wait_analysis(task_id)
        url = self.web_url + "/dashboard?" + urlencode({"id": key})
        print("Quality gate: " + {"OK": "PASSED", "ERROR": "FAILED", "WARN": "WARNING", "NONE": "NOT CONFIGURED"}[gate])
        print("Results: " + url, flush=True)
        atomic_write(scan_dir / "result.json", json.dumps({
            "project_key": key, "task_id": task_id, "quality_gate": gate, "dashboard_url": url,
        }, indent=2) + "\n")
        return gate

    def scan(self, names, check_gate=False):
        infrastructure_failed = False
        gate_failed = False
        for name in names:
            try:
                gate = self.scan_one(name)
                gate_failed = gate_failed or gate != "OK"
            except (PackageError, OSError) as error:
                print("Error scanning " + name + ": " + self.state.redact(str(error)), file=sys.stderr, flush=True)
                infrastructure_failed = True
        if infrastructure_failed:
            return 1
        return 2 if check_gate and gate_failed else 0

    def credentials(self):
        data = self.state.require()
        if not data["admin_initialized"]:
            raise PackageError("Initial setup has not completed. Run ./sonar up again before retrieving the web login.")
        print("Web interface: " + self.web_url)
        print("Username: admin")
        print("Password: " + data["admin_password"])

    def report(self, name):
        from report import Exporter, ExportError, finding_csv, render_html
        # Export existing server data without scanning, changing tokens, or bootstrapping.
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,199}", name):
            raise PackageError("Invalid project folder name.")
        data = self.state.require()
        if not data["admin_initialized"]:
            raise PackageError("Initial setup has not completed. Run ./sonar up first.")
        try:
            report = Exporter(self.client(), "local:" + name, self.web_url,
                              positive_timeout("SONAR_REPORT_TIMEOUT", "600")).collect()
            # Publish only after all API requests and completeness checks succeeded.
            directory = self.state.directory / "reports" / name / uuid.uuid4().hex
            directory.mkdir(parents=True, mode=0o700)
            for filename, content in (("index.html", render_html(report)),
                                      ("issues.csv", finding_csv(report)),
                                      ("security-hotspots.csv", finding_csv(report, hotspot=True))):
                atomic_write(directory / filename, self.state.redact(content))
            print("Report: .state/" + str(directory.relative_to(self.state.directory)) + "/index.html")
            print("CSV files: issues.csv and security-hotspots.csv in the same folder.")
        except ExportError as error:
            raise PackageError(str(error)) from error

    def status(self):
        try:
            status = SonarClient(self.api_url).request("api/system/status").get("status", "UNKNOWN")
        except APIError:
            status = "UNREACHABLE"
        print("SonarQube: " + status)
        print("Web interface: " + self.web_url)
        return 0 if status == "UP" else 1


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise PackageError(message)


def main(argv=None):
    os.umask(0o077)
    try:
        parser = Parser(description=__doc__)
        commands = parser.add_subparsers(dest="command", required=True)
        for name in ("init", "preflight", "bootstrap", "credentials", "status"):
            commands.add_parser(name)
        for name in ("validate", "scan"):
            command = commands.add_parser(name)
            command.add_argument("name", nargs="?")
            command.add_argument("--all", action="store_true", dest="all_projects")
            command.add_argument("--check-gate", action="store_true")
        commands.add_parser("report").add_argument("name")
        arguments = parser.parse_args(argv)
        package = SonarPackage()
        if arguments.command == "init":
            package.state.initialize()
        elif arguments.command == "preflight":
            package.preflight()
        elif arguments.command == "bootstrap":
            package.bootstrap()
        elif arguments.command == "credentials":
            package.credentials()
        elif arguments.command == "status":
            return package.status()
        elif arguments.command == "report":
            package.report(arguments.name)
        else:
            names = package.names(arguments.name, arguments.all_projects)
            package.validate(names)
            if arguments.command == "scan":
                return package.scan(names, arguments.check_gate)
        return 0
    except (PackageError, OSError, UnicodeError) as error:
        print("Error: " + str(error), file=sys.stderr, flush=True)
        return 1
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr, flush=True)
        return 130


if __name__ == "__main__":
    sys.exit(main())
