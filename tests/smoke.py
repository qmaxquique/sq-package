#!/usr/bin/env python3
"""Run a real, isolated Docker smoke test; clean up its own volumes afterward."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import socket
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from sonar import SonarClient


def snapshot(directory):
    return {path.relative_to(directory).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in directory.rglob("*") if path.is_file()}


def main():
    # A path containing spaces also exercises the Bash wrapper's quoting.
    workspace = Path(tempfile.mkdtemp(prefix="sonar smoke "))
    for name in ("sonar", "compose.yaml", "Dockerfile", ".dockerignore"):
        shutil.copy2(str(ROOT / name), str(workspace / name))
    shutil.copytree(str(ROOT / "scripts"), str(workspace / "scripts"), ignore=shutil.ignore_patterns("__pycache__"))
    (workspace / "config" / "projects").mkdir(parents=True)
    shutil.copyfile(str(ROOT / "config" / "defaults.properties"), str(workspace / "config" / "defaults.properties"))
    shutil.copytree(str(ROOT / "tests" / "fixtures"), str(workspace / "projects"))
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    environment = dict(os.environ, SONAR_PORT=str(port), GIT_OPTIONAL_LOCKS="0")
    web_url = "http://localhost:" + str(port)
    (workspace / "config" / "projects" / "alpha.properties").write_text(
        "sonar.sources=src\nsonar.tests=tests\n"
        "sonar.php.coverage.reportPaths=coverage/clover.xml\n"
        "sonar.php.tests.reportPath=coverage/junit.xml\n"
    )
    (workspace / "config" / "projects" / "wordpress.properties").write_text(
        "sonar.sources=wp-content/themes/owned,wp-content/plugins/owned\n"
    )
    for repository in (workspace / "projects").iterdir():
        subprocess.run(["git", "init", "--quiet", str(repository)], check=True, env=environment)
        subprocess.run(["git", "-C", str(repository), "add", "."], check=True, env=environment)
        subprocess.run(["git", "-C", str(repository), "-c", "user.name=Sonar fixture",
                        "-c", "user.email=fixture@example.invalid", "-c", "core.hooksPath=/dev/null",
                        "-c", "commit.gpgsign=false", "commit", "--quiet", "-m", "Fixture"],
                       check=True, env=environment)
    originals = snapshot(workspace / "projects")
    config_originals = snapshot(workspace / "config")
    passed = False

    def cli(*arguments, expected=0):
        print("Smoke: ./sonar " + " ".join(arguments), flush=True)
        result = subprocess.run([str(workspace / "sonar")] + list(arguments), cwd=str(workspace),
                                env=environment, text=True, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, timeout=1200)
        if result.returncode != expected:
            print(result.stdout, flush=True)
            raise AssertionError("Expected exit " + str(expected) + ", got " + str(result.returncode))
        for line in result.stdout.splitlines():
            if line.startswith(("SonarQube is ready", "Quality gate:", "Results:", "Error:")):
                print(line, flush=True)
        return result.stdout

    def credentials():
        return json.loads((workspace / ".state" / "credentials.json").read_text())

    def client():
        return SonarClient(web_url, password=credentials()["admin_password"])

    def measures(key, metrics):
        response = client().request("api/measures/component", {"component": key, "metricKeys": metrics})
        return {item["metric"]: item["value"] for item in response["component"]["measures"]}

    def files(key):
        response = client().request("api/components/tree", {
            "component": key, "qualifiers": "FIL", "strategy": "all", "ps": "500",
        })
        return {item["path"] for item in response["components"]}

    try:
        cli("scan", "--all")
        initial = credentials()
        assert initial["admin_initialized"] is True
        assert not SonarClient(web_url, password="admin").authenticated(), "Initial administrator password still works"
        components = client().request("api/projects/search", {"ps": 100})["components"]
        assert {item["key"] for item in components} == {"local:alpha", "local:beta", "local:wordpress"}
        assert all(item["visibility"] == "private" for item in components)
        assert files("local:alpha") == {"src/Calculator.php"}
        assert files("local:beta") == {"index.php"}
        assert files("local:wordpress") == {
            "wp-content/themes/owned/functions.php", "wp-content/plugins/owned/plugin.php",
        }
        alpha_metrics = measures("local:alpha", "coverage,tests")
        assert 0 < float(alpha_metrics["coverage"]) < 100, alpha_metrics
        assert int(alpha_metrics["tests"]) == 1, alpha_metrics
        cli("up")
        assert credentials() == initial, "Repeated setup changed stored credentials"
        cli("scan", "beta")
        assert credentials() == initial, "Repeated scanning replaced a valid token"

        # A deliberate gate ensures failure is observable on the very first branch
        # analysis, independent of Sonar Way's new-code baseline behavior.
        gate = client().request("api/qualitygates/create", {"name": "Smoke coverage"}, post=True)
        gate_name = gate.get("name", "Smoke coverage")
        client().request("api/qualitygates/create_condition", {
            "gateName": gate_name, "metric": "coverage", "op": "LT", "error": "100",
        }, post=True)
        client().request("api/qualitygates/select", {"gateName": gate_name, "projectKey": "local:alpha"}, post=True)
        output = cli("scan", "alpha")
        assert "Quality gate: FAILED" in output
        cli("scan", "alpha", "--check-gate", expected=2)

        old_token = credentials()["projects"]["beta"]["token"]
        client().request("api/user_tokens/revoke", {
            "name": credentials()["projects"]["beta"]["token_name"],
        }, post=True, sensitive=True)
        cli("scan", "beta")
        assert credentials()["projects"]["beta"]["token"] != old_token, "Revoked token was not replaced"

        history = client().request("api/project_analyses/search", {"project": "local:alpha"})["analyses"]
        assert len(history) >= 3
        cli("down")
        cli("up")
        preserved = client().request("api/project_analyses/search", {"project": "local:alpha"})["analyses"]
        assert {item["key"] for item in history} == {item["key"] for item in preserved}
        cli("status")
        before_report = credentials()
        cli("report", "alpha")
        report_files = list((workspace / ".state" / "reports" / "alpha").glob("*/index.html"))
        assert len(report_files) == 1
        assert "FAILED" in report_files[0].read_text()
        assert (report_files[0].parent / "issues.csv").is_file()
        assert (report_files[0].parent / "security-hotspots.csv").is_file()
        assert credentials() == before_report, "Report changed credentials"
        cli("report", "missing", expected=1)
        cli("scan", "missing", expected=1)
        assert snapshot(workspace / "projects") == originals, "A source or Git file changed"
        assert snapshot(workspace / "config") == config_originals, "Project configuration changed"

        cli("reset", "--yes")
        assert not (workspace / ".state").exists()
        assert snapshot(workspace / "projects") == originals, "Reset removed source files"
        assert snapshot(workspace / "config") == config_originals, "Reset removed configuration"
        passed = True
        print("Smoke passed on " + platform.system() + "/" + platform.machine() + ": "
              "3 projects, coverage/tests import, gate modes, token recovery, restart persistence, "
              "HTML/CSV export, unchanged sources/Git, and reset preservation.", flush=True)
        return 0
    finally:
        cleaned = passed
        if not passed and (workspace / ".state").exists():
            print("Cleaning isolated smoke stack...", flush=True)
            result = subprocess.run([str(workspace / "sonar"), "reset", "--yes"], cwd=str(workspace),
                                    env=environment, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    text=True, timeout=120)
            cleaned = result.returncode == 0
            if not cleaned:
                print("Cleanup failed; workspace preserved at " + str(workspace), file=sys.stderr)
        if cleaned:
            shutil.rmtree(str(workspace))


if __name__ == "__main__":
    sys.exit(main())
