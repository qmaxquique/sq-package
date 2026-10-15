import base64
from contextlib import redirect_stdout, redirect_stderr
import io
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import sonar


class FakeSonar:
    """HTTP fixture exercising real request encoding and authentication."""
    def __init__(self):
        self.password = "admin"
        self.projects = {}
        self.tokens = {}
        self.password_changes = 0
        self.token_generations = 0
        self.tasks = {}
        self.gates = {}
        self.queries = []
        self.status = "UP"
        self.fail_next_task = False
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                self.handle_api(False)

            def do_POST(self):
                self.handle_api(True)

            def handle_api(self, post):
                split = urlsplit(self.path)
                if post:
                    content = self.rfile.read(int(self.headers.get("Content-Length", 0))).decode()
                else:
                    content = split.query
                parameters = {key: values[0] for key, values in parse_qs(content).items()}
                fixture.queries.append((split.path, parameters))
                authorization = self.headers.get("Authorization", "")
                basic = "Basic " + base64.b64encode(("admin:" + fixture.password).encode()).decode()
                valid = authorization == basic
                if authorization.startswith("Bearer "):
                    valid = fixture.tokens.get(authorization[7:], {}).get("valid", False)
                path = split.path
                if path == "/api/system/status":
                    self.respond({"status": fixture.status})
                elif path == "/api/authentication/validate":
                    self.respond({"valid": valid})
                elif not valid:
                    self.respond({"errors": [{"msg": "Unauthorized"}]}, 401)
                elif path == "/api/users/change_password":
                    if parameters["previousPassword"] != fixture.password:
                        self.respond({}, 400)
                        return
                    fixture.password = parameters["password"]
                    fixture.password_changes += 1
                    self.respond({})
                elif path == "/api/settings/set":
                    self.respond({})
                elif path == "/api/projects/search":
                    key = parameters["projects"]
                    self.respond({"components": [fixture.projects[key]] if key in fixture.projects else []})
                elif path == "/api/projects/create":
                    key = parameters["project"]
                    fixture.projects[key] = {"key": key, "visibility": parameters["visibility"]}
                    self.respond({"project": fixture.projects[key]})
                elif path == "/api/projects/update_visibility":
                    fixture.projects[parameters["project"]]["visibility"] = parameters["visibility"]
                    self.respond({})
                elif path == "/api/user_tokens/search":
                    self.respond({"userTokens": list(fixture.tokens.values())})
                elif path == "/api/user_tokens/revoke":
                    fixture.tokens = {key: value for key, value in fixture.tokens.items()
                                      if value["name"] != parameters["name"]}
                    self.respond({})
                elif path == "/api/user_tokens/generate":
                    fixture.token_generations += 1
                    token = "sqp_fake_" + str(fixture.token_generations)
                    fixture.tokens[token] = {"name": parameters["name"], "valid": True,
                                              "projectKey": parameters["projectKey"], "type": parameters["type"]}
                    self.respond({"token": token})
                elif path == "/api/ce/task":
                    if fixture.fail_next_task:
                        fixture.fail_next_task = False
                        self.respond({}, 503)
                    else:
                        self.respond({"task": fixture.tasks[parameters["id"]]})
                elif path == "/api/qualitygates/project_status":
                    self.respond({"projectStatus": {"status": fixture.gates[parameters["analysisId"]]}})
                else:
                    self.respond({}, 404)

            def respond(self, payload, status=200):
                data = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = "http://127.0.0.1:" + str(self.server.server_address[1])
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()


class PackageFixture(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="sonar-unit-")
        self.root = Path(self.temporary.name)
        self.state = self.root / "state"
        self.projects = self.root / "projects"
        self.config = self.root / "config"
        self.projects.mkdir()
        (self.config / "projects").mkdir(parents=True)
        shutil.copyfile(str(ROOT / "config" / "defaults.properties"), str(self.config / "defaults.properties"))
        self.repository = self.projects / "app"
        self.repository.mkdir()
        (self.repository / "index.php").write_text("<?php echo 'hello';\n")
        self.package = sonar.SonarPackage(self.state, self.projects, self.config)

    def tearDown(self):
        self.temporary.cleanup()

    def override(self, content):
        path = self.config / "projects" / "app.properties"
        path.write_text(content)
        return path


class ConfigurationTests(PackageFixture):
    def test_generic_repository_requires_no_configuration(self):
        self.assertEqual(self.package.names("app"), ["app"])
        self.assertEqual(self.package.properties("app")["sonar.sources"], ".")

    def test_python_only_repository_requires_no_configuration(self):
        (self.repository / "index.php").unlink()
        (self.repository / "app.py").write_text("print('hello')\n")
        properties = self.package.properties("app")
        self.assertIn("**/*.py", properties["sonar.inclusions"])
        self.assertNotIn("sonar.python.coverage.reportPaths", properties)
        self.assertNotIn("sonar.python.xunit.reportPath", properties)

    def test_mixed_repository_defaults_and_explicit_language_selection(self):
        (self.repository / "app.py").write_text("print('hello')\n")
        properties = self.package.properties("app")
        for pattern in ("**/*.py", "**/*.php"):
            self.assertIn(pattern, properties["sonar.inclusions"])
        self.override("sonar.inclusions=**/*.py\nsonar.python.version=3.11,3.12\n")
        self.assertEqual(self.package.properties("app")["sonar.python.version"], "3.11,3.12")
        (self.repository / "app.py").unlink()
        with self.assertRaisesRegex(sonar.PackageError, "No PHP or Python"):
            self.package.properties("app")

    def test_python_environments_and_caches_do_not_count_as_sources(self):
        (self.repository / "index.php").unlink()
        for folder in (".venv", "venv", "nested/site-packages", "__pycache__",
                       ".pytest_cache", ".mypy_cache", ".ruff_cache", ".tox", ".nox", "app.egg-info"):
            path = self.repository / folder / "dependency.py"
            path.parent.mkdir(parents=True)
            path.write_text("print('dependency')\n")
        with self.assertRaisesRegex(sonar.PackageError, "No PHP or Python"):
            self.package.properties("app")
        (self.repository / "app.py").write_text("print('owned')\n")
        properties = self.package.properties("app")
        for key in ("sonar.exclusions", "sonar.test.exclusions"):
            self.assertTrue(all(any(sonar.matches(path.relative_to(self.repository).as_posix(), p)
                                    for p in sonar.comma_values(properties[key]))
                                for path in self.repository.rglob("dependency.py")))

    def test_python_tests_are_separated_from_production_sources(self):
        (self.repository / "tests").mkdir()
        (self.repository / "tests" / "test_app.py").write_text("def test_app():\n    assert True\n")
        self.override("sonar.tests=tests\n")
        self.assertIn("tests/**", self.package.properties("app")["sonar.exclusions"])
        (self.repository / "index.php").unlink()
        with self.assertRaisesRegex(sonar.PackageError, "No PHP or Python"):
            self.package.properties("app")

    def test_custom_python_suffixes_still_respect_inclusions(self):
        (self.repository / "index.php").unlink()
        (self.repository / "app.pyw").write_text("print('hello')\n")
        self.override("sonar.python.file.suffixes=.py,.pyw\n")
        with self.assertRaises(sonar.PackageError):
            self.package.properties("app")
        self.override("sonar.python.file.suffixes=.py,.pyw\nsonar.inclusions=**/*.pyw\n")
        self.assertEqual(self.package.properties("app")["sonar.python.file.suffixes"], ".py,.pyw")

    def test_python_reports_are_optional_and_validated(self):
        for filename in ("coverage.xml", "coverage-other.xml", "junit.xml"):
            (self.repository / filename).write_text("<report/>\n")
        self.override("sonar.python.coverage.reportPaths=coverage.xml,coverage-other.xml\n"
                      "sonar.python.xunit.reportPath=junit.xml\n")
        properties = self.package.properties("app")
        self.assertEqual(properties["sonar.python.coverage.reportPaths"], "coverage.xml,coverage-other.xml")
        self.assertEqual(properties["sonar.python.xunit.reportPath"], "junit.xml")
        for key in ("sonar.python.coverage.reportPaths", "sonar.python.xunit.reportPath"):
            for value in ("", "missing.xml", "../outside.xml", "/tmp/report.xml", ".", "*.xml"):
                with self.subTest(key=key, value=value):
                    self.override(key + "=" + value + "\n")
                    with self.assertRaises(sonar.PackageError):
                        self.package.properties("app")
        self.override("sonar.python.xunit.reportPath=junit.xml,coverage.xml\n")
        with self.assertRaisesRegex(sonar.PackageError, "Invalid report path"):
            self.package.properties("app")

    def test_python_example_is_usable_without_optional_paths(self):
        (self.repository / "index.php").unlink()
        (self.repository / "app.py").write_text("print('hello')\n")
        self.override((ROOT / "config/examples/python.properties").read_text())
        self.assertEqual(self.package.properties("app")["sonar.sources"], ".")

    def test_missing_repository_or_ambiguous_arguments(self):
        for name, all_projects in (("missing", False), (None, False), ("app", True), ("../app", False)):
            with self.subTest(name=name, all_projects=all_projects), self.assertRaises(sonar.PackageError):
                self.package.names(name, all_projects)

    def test_all_is_sorted_and_ignores_hidden_directories(self):
        (self.projects / "beta").mkdir()
        (self.projects / ".hidden").mkdir()
        self.assertEqual(self.package.names(all_projects=True), ["app", "beta"])

    def test_repository_symlink_is_rejected(self):
        (self.projects / "alias").symlink_to(self.repository, target_is_directory=True)
        with self.assertRaises(sonar.PackageError):
            self.package.names("alias")

    def test_named_scan_requires_exact_folder_capitalization(self):
        with self.assertRaises(sonar.PackageError):
            self.package.names("APP")

    def test_package_owned_settings_cannot_be_overridden(self):
        for key in ("sonar.token", "sonar.projectKey", "sonar.host.url", "sonar.projectBaseDir",
                    "sonar.working.directory", "sonar.scanner.dumpToFile", "sonar.branch.name", "project.settings"):
            with self.subTest(key=key):
                self.override(key + "=value\n")
                with self.assertRaises(sonar.PackageError):
                    self.package.properties("app")

    def test_excluded_dependencies_do_not_count_as_sources(self):
        (self.repository / "index.php").unlink()
        (self.repository / "vendor").mkdir()
        (self.repository / "vendor" / "dependency.php").write_text("<?php echo 1;\n")
        with self.assertRaisesRegex(sonar.PackageError, "No PHP"):
            self.package.properties("app")

    def test_wordpress_selects_owned_code(self):
        (self.repository / "wp-content" / "themes" / "owned").mkdir(parents=True)
        (self.repository / "wp-content" / "themes" / "owned" / "functions.php").write_text("<?php echo 1;\n")
        self.override("sonar.sources=wp-content/themes/owned\n")
        self.assertEqual(self.package.properties("app")["sonar.sources"], "wp-content/themes/owned")
        self.override("sonar.sources=wp-content/themes/YOUR_THEME\n")
        with self.assertRaisesRegex(sonar.PackageError, "does not exist"):
            self.package.properties("app")

    def test_sources_and_reports_must_exist_inside_repository(self):
        for content in ("sonar.sources=missing\n", "sonar.sources=../\n", "sonar.sources=/tmp\n",
                        "sonar.php.coverage.reportPaths=missing.xml\n", "sonar.php.tests.reportPath=../x.xml\n"):
            with self.subTest(content=content):
                self.override(content)
                with self.assertRaises(sonar.PackageError):
                    self.package.properties("app")

    def test_existing_reports_are_imported_only_when_configured(self):
        (self.repository / "clover.xml").write_text("<coverage/>\n")
        self.assertNotIn("sonar.php.coverage.reportPaths", self.package.properties("app"))
        self.override("sonar.php.coverage.reportPaths=clover.xml\n")
        self.assertEqual(self.package.properties("app")["sonar.php.coverage.reportPaths"], "clover.xml")

    def test_test_directories_are_removed_from_source_set(self):
        (self.repository / "tests").mkdir()
        (self.repository / "tests" / "ExampleTest.php").write_text("<?php echo 1;\n")
        self.override("sonar.tests=tests\n")
        self.assertIn("tests/**", self.package.properties("app")["sonar.exclusions"])

    def test_shared_test_root_requires_file_patterns(self):
        self.override("sonar.tests=.\n")
        with self.assertRaises(sonar.PackageError):
            self.package.properties("app")
        self.override("sonar.tests=.\nsonar.test.inclusions=**/*Test.php\n")
        self.assertIn("**/*Test.php", self.package.properties("app")["sonar.exclusions"])

    def test_properties_format_and_round_trip(self):
        path = self.override("# comment\nsonar.sources : .\nsonar.exclusions=**/vendor/**,\\\n  **/cache/**\nsonar.projectDescription=Name\\: one \\u00e9\n")
        values = sonar.read_properties(path)
        self.assertEqual(values["sonar.exclusions"], "**/vendor/**,**/cache/**")
        self.assertEqual(values["sonar.projectDescription"], "Name: one é")
        path.write_text(sonar.write_properties(values))
        self.assertEqual(sonar.read_properties(path), values)

    def test_ant_patterns_distinguish_directory_boundaries(self):
        self.assertTrue(sonar.matches("index.php", "**/*.php"))
        self.assertTrue(sonar.matches("src/vendor/foo.php", "**/vendor/**"))
        self.assertTrue(sonar.matches("vendor/foo.php", "**/vendor/**"))
        self.assertFalse(sonar.matches("src/nested/foo.php", "src/*.php"))

    def test_invalid_timeouts_fail_early(self):
        for value in ("0", "-1", "nan", "inf", "abc"):
            with self.subTest(value=value), patch.dict(os.environ, {"SONAR_STARTUP_TIMEOUT": value}):
                with self.assertRaises(sonar.PackageError):
                    sonar.SonarPackage(self.state, self.projects, self.config)

    def test_kernel_preflight_is_read_only_and_rejects_low_limits(self):
        kernel = self.root / "sysctl"
        (kernel / "vm").mkdir(parents=True)
        (kernel / "fs").mkdir()
        maps = kernel / "vm" / "max_map_count"
        files = kernel / "fs" / "file-max"
        maps.write_text("262144\n")
        files.write_text("131072\n")
        with self.assertRaisesRegex(sonar.PackageError, "vm.max_map_count=262144"):
            self.package.preflight(kernel)
        self.assertEqual(maps.read_text(), "262144\n")
        self.assertFalse(self.package.state.path.exists())
        maps.write_text("524288\n")
        self.package.preflight(kernel)
        files.write_text("1000\n")
        with self.assertRaisesRegex(sonar.PackageError, "fs.file-max=1000"):
            self.package.preflight(kernel)

    def test_long_repository_names_are_rejected_before_filesystem_access(self):
        with self.assertRaisesRegex(sonar.PackageError, "maximum 200"):
            self.package.names("a" * 201)


class BootstrapTests(PackageFixture):
    def setUp(self):
        super().setUp()
        self.api = FakeSonar()
        self.package.api_url = self.api.url
        self.package.state.initialize()

    def tearDown(self):
        self.api.close()
        super().tearDown()

    def bootstrap(self):
        with redirect_stdout(io.StringIO()):
            self.package.bootstrap()

    def test_state_is_private_and_initialization_preserves_credentials(self):
        before = self.package.state.path.read_bytes()
        self.package.state.initialize()
        self.assertEqual(before, self.package.state.path.read_bytes())
        self.assertEqual(stat.S_IMODE(self.state.stat().st_mode), 0o700)
        for path in (self.state / "credentials.json", self.state / "runtime.env"):
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_credentials_are_only_displayed_after_setup_completes(self):
        output = io.StringIO()
        with redirect_stdout(output), self.assertRaisesRegex(sonar.PackageError, "has not completed"):
            self.package.credentials()
        self.assertEqual(output.getvalue(), "")
        self.bootstrap()
        with redirect_stdout(output):
            self.package.credentials()
        self.assertIn(self.package.state.data["admin_password"], output.getvalue())

    def test_bootstrap_rotates_password_once_and_encodes_special_characters(self):
        self.package.state.data["admin_password"] = "Aa1! with &=+ spaces"
        self.package.state.save()
        self.bootstrap()
        self.bootstrap()
        self.assertEqual(self.api.password_changes, 1)
        self.assertEqual(self.api.password, "Aa1! with &=+ spaces")

    def test_podman_setup_prints_matching_credentials_command(self):
        with patch.dict(os.environ, {"SONAR_ENGINE": "podman"}):
            package = sonar.SonarPackage(self.state, self.projects, self.config, self.api.url)
        output = io.StringIO()
        with redirect_stdout(output):
            package.bootstrap()
        self.assertIn("./sonar --engine podman credentials", output.getvalue())

    def test_interrupted_password_setup_resumes_without_rotating_again(self):
        with patch.object(self.package.state, "save", side_effect=OSError("interrupted")):
            with self.assertRaises(OSError):
                self.bootstrap()
        replacement = sonar.SonarPackage(self.state, self.projects, self.config, self.api.url)
        with redirect_stdout(io.StringIO()):
            replacement.bootstrap()
        self.assertEqual(self.api.password_changes, 1)
        self.assertTrue(replacement.state.data["admin_initialized"])

    def test_project_registration_and_tokens_are_idempotent(self):
        self.bootstrap()
        first = self.package.ensure_project("app")
        second = self.package.ensure_project("app")
        other = self.package.ensure_project("beta")
        self.assertEqual(first, second)
        self.assertNotEqual(first[0], other[0])
        self.assertEqual(self.api.token_generations, 2)
        self.assertEqual(self.api.projects["local:app"]["visibility"], "private")
        self.assertEqual(self.api.tokens[first[1]]["type"], "PROJECT_ANALYSIS_TOKEN")

    def test_revoked_project_token_is_replaced(self):
        self.bootstrap()
        key, original = self.package.ensure_project("app")
        self.api.tokens[original]["valid"] = False
        replacement_key, replacement = self.package.ensure_project("app")
        self.assertEqual(key, replacement_key)
        self.assertNotEqual(original, replacement)
        self.assertNotIn(original, self.api.tokens)
        self.assertEqual(len(self.api.tokens), 1)

    def test_orphaned_token_is_recovered(self):
        self.bootstrap()
        key, original = self.package.ensure_project("app")
        self.package.state.data["projects"].pop("app")
        self.package.state.save()
        self.package.ensure_project("app")
        self.assertEqual(len(self.api.projects), 1)
        self.assertNotIn(original, self.api.tokens)
        self.assertEqual(len(self.api.tokens), 1)

    def test_changed_administrator_password_is_not_silently_reset(self):
        self.bootstrap()
        self.api.password = "changed-outside-package"
        with self.assertRaisesRegex(sonar.PackageError, "no longer match"):
            self.bootstrap()
        self.assertEqual(self.api.password_changes, 1)

    def test_processing_uses_exact_analysis_id(self):
        self.bootstrap()
        self.api.tasks["new-task"] = {"status": "SUCCESS", "analysisId": "new-analysis"}
        self.api.gates.update({"old-analysis": "OK", "new-analysis": "ERROR"})
        self.assertEqual(self.package.wait_analysis("new-task"), "ERROR")
        self.assertIn(("/api/qualitygates/project_status", {"analysisId": "new-analysis"}), self.api.queries)

    def test_failed_processing_is_an_error(self):
        self.bootstrap()
        self.api.tasks["failed-task"] = {"status": "FAILED", "errorMessage": "Analyzer failed"}
        with self.assertRaisesRegex(sonar.PackageError, "Analyzer failed"):
            self.package.wait_analysis("failed-task")

    def test_processing_and_startup_have_bounded_timeouts(self):
        self.bootstrap()
        self.api.tasks["pending"] = {"status": "PENDING"}
        self.package.analysis_timeout = 0.01
        with self.assertRaisesRegex(sonar.PackageError, "processing timed out"):
            self.package.wait_analysis("pending")
        self.api.status = "STARTING"
        self.package.startup_timeout = 0.01
        with redirect_stdout(io.StringIO()), self.assertRaisesRegex(sonar.PackageError, "startup timed out"):
            self.package.wait_ready()

    def test_gate_failure_is_optional_and_infrastructure_failure_takes_priority(self):
        with patch.object(self.package, "scan_one", return_value="ERROR"):
            self.assertEqual(self.package.scan(["app"]), 0)
            self.assertEqual(self.package.scan(["app"], check_gate=True), 2)
        with patch.object(self.package, "scan_one", side_effect=[sonar.PackageError("failed"), "ERROR"]):
            with redirect_stderr(io.StringIO()):
                self.assertEqual(self.package.scan(["app", "beta"], check_gate=True), 1)

    def test_scanner_output_redacts_credentials_and_propagates_failure(self):
        binary = self.root / "bin"
        binary.mkdir()
        script = binary / "sonar-scanner"
        script.write_text("#!/usr/bin/env python3\nimport os\nprint(os.environ['SONAR_TOKEN'])\nraise SystemExit(7)\n")
        script.chmod(0o755)
        token = "sqp_fake_secret"
        self.package.state.data["projects"]["app"] = {"key": "local:app", "token_name": "test", "token": token}
        output = io.StringIO()
        with patch.dict(os.environ, {"PATH": str(binary) + os.pathsep + os.environ["PATH"]}):
            with redirect_stdout(output), self.assertRaisesRegex(sonar.PackageError, "exit status 7"):
                self.package.run_scanner(self.repository, self.root / "settings", token)
        self.assertIn("[redacted]", output.getvalue())
        self.assertNotIn(token, output.getvalue())

    def test_scanner_execution_timeout(self):
        binary = self.root / "bin"
        binary.mkdir()
        script = binary / "sonar-scanner"
        script.write_text("#!/usr/bin/env python3\nimport time\ntime.sleep(30)\n")
        script.chmod(0o755)
        self.package.scan_timeout = 0.02
        with patch.dict(os.environ, {"PATH": str(binary) + os.pathsep + os.environ["PATH"]}):
            with self.assertRaisesRegex(sonar.PackageError, "Scanner timed out"):
                self.package.run_scanner(self.repository, self.root / "settings", "fake")


class WrapperTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="sonar shell ")
        self.root = Path(self.temporary.name)
        shutil.copy2(str(ROOT / "sonar"), str(self.root / "sonar"))
        self.binary = self.root / "bin"
        self.binary.mkdir()
        docker = self.binary / "docker"
        docker.write_text("""#!/usr/bin/env bash
printf '%s\\n' "$*" >> "$DOCKER_CALLS"
if [ "$1" = info ]; then exit "${FAKE_DOCKER_DOWN:-0}"; fi
if [ "$1" = context ]; then printf '%s\\n' "${FAKE_DOCKER_ENDPOINT:-unix:///fake/docker.sock}"; fi
exit 0
""")
        docker.chmod(0o755)
        podman = self.binary / "podman"
        podman.write_text("""#!/usr/bin/env bash
printf '%s\\n' "$*" >> "$PODMAN_CALLS"
if [ "$1" = info ]; then
    if [ "${2:-}" = --format ]; then
        case "${3:-}" in
            *ServiceIsRemote*) printf '%s\\n' "${FAKE_PODMAN_REMOTE:-true}" ;;
            *) printf '%s\\n' "${FAKE_PODMAN_ROOTLESS:-true}" ;;
        esac
    fi
    exit "${FAKE_PODMAN_DOWN:-0}"
fi
if [ "$1" = system ]; then printf '%b' "${FAKE_PODMAN_CONNECTIONS:-}"; fi
if [ "$1" = compose ] && [ "${2:-}" = version ]; then exit "${FAKE_PODMAN_COMPOSE_DOWN:-0}"; fi
if [ "$1" = compose ]; then
    printf 'userns=%s docker_host=%s docker_context=%s\\n' "${SONAR_PODMAN_USERNS:-}" "${DOCKER_HOST:-unset}" "${DOCKER_CONTEXT:-unset}" >> "$PODMAN_CALLS"
fi
exit 0
""")
        podman.chmod(0o755)
        self.calls = self.root / "docker-calls"
        self.podman_calls = self.root / "podman-calls"
        self.environment = dict(os.environ, PATH=str(self.binary) + os.pathsep + os.environ["PATH"],
                                DOCKER_CALLS=str(self.calls), PODMAN_CALLS=str(self.podman_calls))
        for name in ("DOCKER_HOST", "DOCKER_CONTEXT", "SONAR_ENGINE", "SONAR_STATE_PATH",
                     "CONTAINER_HOST", "CONTAINER_CONNECTION"):
            self.environment.pop(name, None)

    def tearDown(self):
        self.temporary.cleanup()

    def cli(self, *arguments):
        return subprocess.run([str(self.root / "sonar")] + list(arguments), cwd=str(self.root),
                              env=self.environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    def test_help_needs_no_docker_and_creates_no_state(self):
        result = self.cli("help")
        self.assertEqual(result.returncode, 0)
        self.assertFalse(self.calls.exists())
        self.assertFalse((self.root / ".state").exists())

    def test_reset_requires_explicit_flag_before_contacting_docker(self):
        result = self.cli("reset")
        self.assertEqual(result.returncode, 1)
        self.assertFalse(self.calls.exists())

    def test_reset_preserves_sources_and_configuration(self):
        state = self.root / ".state"
        state.mkdir()
        (state / "credentials.json").write_text("fake private state")
        (self.root / "projects").mkdir()
        source = self.root / "projects" / "index.php"
        source.write_text("<?php echo 1;")
        (self.root / "config").mkdir()
        configuration = self.root / "config" / "example.properties"
        configuration.write_text("sonar.sources=.")
        self.assertEqual(self.cli("reset", "--yes").returncode, 0)
        self.assertFalse(state.exists())
        self.assertTrue(source.exists())
        self.assertTrue(configuration.exists())
        self.assertIn("down --volumes --remove-orphans", self.calls.read_text())

    def test_active_lock_blocks_other_operations(self):
        lock = self.root / ".state" / "lock"
        lock.mkdir(parents=True)
        (lock / "pid").write_text(str(os.getpid()) + "\n")
        result = self.cli("down")
        self.assertEqual(result.returncode, 1)
        self.assertIn("Another package operation", result.stderr)
        self.assertTrue(lock.exists())

    def test_dead_owner_lock_recovers(self):
        lock = self.root / ".state" / "lock"
        lock.mkdir(parents=True)
        (lock / "pid").write_text("987654321\n")
        self.assertEqual(self.cli("down").returncode, 0)
        self.assertFalse(lock.exists())

    def test_stopped_daemon_has_actionable_error(self):
        self.environment["FAKE_DOCKER_DOWN"] = "1"
        result = self.cli("up")
        self.assertEqual(result.returncode, 1)
        self.assertIn("Docker is not running", result.stderr)

    def test_remote_docker_context_is_rejected(self):
        self.environment["FAKE_DOCKER_ENDPOINT"] = "ssh://remote.example"
        result = self.cli("up")
        self.assertEqual(result.returncode, 1)
        self.assertIn("local Docker context", result.stderr)

    def test_state_symlink_is_rejected(self):
        destination = self.root / "other-state"
        destination.mkdir()
        (self.root / ".state").symlink_to(destination, target_is_directory=True)
        result = self.cli("down")
        self.assertEqual(result.returncode, 1)
        self.assertIn("symbolic link", result.stderr)
        self.assertTrue(destination.exists())

    def test_port_validation_runs_before_compose(self):
        self.environment["SONAR_PORT"] = "70000"
        result = self.cli("up")
        self.assertEqual(result.returncode, 1)
        self.assertIn("between 1 and 65535", result.stderr)

    def test_default_engine_keeps_docker_state_layout(self):
        self.assertEqual(self.cli("down").returncode, 0)
        self.assertTrue((self.root / ".state" / "stack-name").is_file())
        self.assertFalse(self.podman_calls.exists())
        self.assertNotIn("compose.podman.yaml", self.calls.read_text())

    def test_explicit_podman_never_contacts_docker(self):
        result = self.cli("--engine", "podman", "down")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.calls.exists())
        state = self.root / ".state" / "podman"
        self.assertTrue((state / "stack-name").read_text().strip().endswith("-podman"))
        calls = self.podman_calls.read_text()
        self.assertIn("compose.podman.yaml", calls)
        self.assertIn(str(state / "runtime.env"), calls)

    def test_podman_can_be_selected_via_environment_and_overridden(self):
        self.environment["SONAR_ENGINE"] = "podman"
        self.assertEqual(self.cli("down").returncode, 0)
        self.assertEqual(self.cli("--engine=docker", "down").returncode, 0)
        self.assertTrue(self.calls.exists())
        self.assertTrue(self.podman_calls.exists())

    def test_invalid_engine_fails_before_any_runtime_calls(self):
        for arguments in (("--engine",), ("--engine", "other", "up"), ("--engine=", "up")):
            result = self.cli(*arguments)
            self.assertEqual(result.returncode, 1)
        self.assertFalse(self.calls.exists())
        self.assertFalse(self.podman_calls.exists())

    def test_stopped_podman_has_actionable_error(self):
        self.environment["FAKE_PODMAN_DOWN"] = "1"
        result = self.cli("--engine", "podman", "up")
        self.assertEqual(result.returncode, 1)
        self.assertIn("Podman is not running", result.stderr)

    def test_podman_requires_compose_provider(self):
        self.environment["FAKE_PODMAN_COMPOSE_DOWN"] = "1"
        result = self.cli("--engine", "podman", "up")
        self.assertEqual(result.returncode, 1)
        self.assertIn("PODMAN_COMPOSE_PROVIDER", result.stderr)

    def test_podman_rejects_remote_connections(self):
        for value in ("ssh://user@remote.example/run/podman.sock", "tcp://remote.example:8080"):
            self.environment["CONTAINER_HOST"] = value
            result = self.cli("--engine", "podman", "up")
            self.assertEqual(result.returncode, 1)
            self.assertIn("remote Podman hosts are unsupported", result.stderr)

    def test_podman_accepts_local_machine_connection(self):
        self.environment["FAKE_PODMAN_CONNECTIONS"] = "machine\\tssh://core@127.0.0.1:1234/run/user/501/podman.sock\\ttrue\\ttrue\\n"
        result = self.cli("--engine", "podman", "down")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_podman_accepts_explicit_known_machine_endpoint(self):
        self.environment["CONTAINER_HOST"] = "ssh://core@127.0.0.1:1234/run/podman.sock"
        self.environment["FAKE_PODMAN_CONNECTIONS"] = "machine\\tssh://core@127.0.0.1:1234/run/podman.sock\\ttrue\\ttrue\\n"
        result = self.cli("--engine", "podman", "down")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_podman_user_namespace_and_docker_context_isolation(self):
        self.environment.update(DOCKER_HOST="unix:///docker.sock", DOCKER_CONTEXT="docker-desktop")
        result = self.cli("--engine", "podman", "down")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("userns=keep-id:uid={},gid={} docker_host=unset docker_context=unset".format(
            os.getuid(), os.getgid()), self.podman_calls.read_text())
        self.environment["FAKE_PODMAN_ROOTLESS"] = "false"
        self.assertEqual(self.cli("--engine", "podman", "down").returncode, 0)
        self.assertIn("userns=host", self.podman_calls.read_text())

    def test_podman_builds_directly_and_forwards_scan_flags(self):
        result = self.cli("--engine", "podman", "scan", "app", "--check-gate")
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.podman_calls.read_text()
        self.assertIn("build --format docker --quiet --tag localhost/", calls)
        self.assertIn("run --rm --no-deps -T tools scan app --check-gate", calls)
        self.assertFalse(self.calls.exists())

    def test_podman_selected_connection_cannot_bypass_remote_check(self):
        self.environment["CONTAINER_CONNECTION"] = "remote"
        self.environment["FAKE_PODMAN_CONNECTIONS"] = "local\\tunix:///local.sock\\ttrue\\tfalse\\nremote\\tssh://user@remote.example/run/podman.sock\\tfalse\\tfalse\\n"
        result = self.cli("--engine", "podman", "down")
        self.assertEqual(result.returncode, 1)
        self.assertIn("remote Podman hosts are unsupported", result.stderr)

    def test_native_linux_podman_ignores_unused_remote_connection(self):
        self.environment["FAKE_PODMAN_REMOTE"] = "false"
        self.environment["FAKE_PODMAN_CONNECTIONS"] = "saved\\tssh://user@remote.example/run/podman.sock\\ttrue\\tfalse\\n"
        result = self.cli("--engine", "podman", "down")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("system connection list", self.podman_calls.read_text())

    def test_reset_only_deletes_selected_engine_state(self):
        docker_state = self.root / ".state"
        podman_state = docker_state / "podman"
        podman_state.mkdir(parents=True)
        (docker_state / "credentials.json").write_text("Docker private state")
        (podman_state / "credentials.json").write_text("Podman private state")
        self.assertEqual(self.cli("reset", "--yes").returncode, 0)
        self.assertEqual((podman_state / "credentials.json").read_text(), "Podman private state")
        self.assertFalse((docker_state / "credentials.json").exists())
        (docker_state / "credentials.json").write_text("Docker private state")
        self.assertEqual(self.cli("--engine", "podman", "reset", "--yes").returncode, 0)
        self.assertFalse(podman_state.exists())
        self.assertEqual((docker_state / "credentials.json").read_text(), "Docker private state")

    def test_podman_uses_shared_package_lock(self):
        lock = self.root / ".state" / "lock"
        lock.mkdir(parents=True)
        (lock / "pid").write_text(str(os.getpid()))
        result = self.cli("--engine", "podman", "down")
        self.assertEqual(result.returncode, 1)
        self.assertIn("Another package operation", result.stderr)


class InvalidStateTests(PackageFixture):
    def test_corrupted_state_is_not_overwritten_or_exposed(self):
        self.state.mkdir()
        path = self.state / "credentials.json"
        path.write_text('{"private_secret": "DO_NOT_PRINT", BROKEN}')
        with self.assertRaises(sonar.PackageError) as caught:
            sonar.State(self.state)
        self.assertNotIn("DO_NOT_PRINT", str(caught.exception))
        self.assertIn("DO_NOT_PRINT", path.read_text())

    def test_state_file_symlink_is_rejected(self):
        self.state.mkdir()
        destination = self.root / "external"
        destination.write_text("external")
        (self.state / "credentials.json").symlink_to(destination)
        with self.assertRaises(sonar.PackageError):
            sonar.State(self.state)
        self.assertEqual(destination.read_text(), "external")


if __name__ == "__main__":
    unittest.main()
