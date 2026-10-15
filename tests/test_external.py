import io
import json
import shutil
import stat
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch
import unittest

import test_package as fixtures
import sonar
import test_report


class ExternalTests(fixtures.PackageFixture):
    def setUp(self):
        super().setUp()
        self.source = self.root / "outside checkout"
        (self.source / "src").mkdir(parents=True)
        (self.source / "src" / "app.py").write_text("print('uncommitted')\n")
        self.source = self.source.resolve()
        self.package.select_external(self.source, str(self.source))

    def test_external_source_uses_its_own_scope_and_reports(self):
        (self.source / "coverage.xml").write_text("<coverage/>\n")
        (self.config / "projects" / "external.properties").write_text(
            "sonar.sources=src\nsonar.python.coverage.reportPaths=coverage.xml\n")
        self.assertEqual(self.package.names("external"), ["external"])
        self.assertEqual(self.package.repository("external"), self.source)
        self.assertEqual(self.package.properties("external")["sonar.sources"], "src")
        (self.source / "coverage.xml").unlink()
        with self.assertRaisesRegex(sonar.PackageError, "does not exist"):
            self.package.properties("external")

    def test_private_state_initializes_on_shares_where_chmod_loses_ownership(self):
        with patch("sonar.os.chmod", side_effect=PermissionError("VM share rewrites ownership")):
            self.package.state.initialize()
            before = self.package.state.path.read_bytes()
            self.package.state.initialize()
        self.assertEqual(before, self.package.state.path.read_bytes())
        self.assertEqual(stat.S_IMODE(self.state.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(self.package.state.path.stat().st_mode), 0o600)

    def test_loose_existing_state_permissions_are_still_restricted(self):
        self.package.state.initialize()
        before = self.package.state.path.read_bytes()
        self.state.chmod(0o755)
        self.package.state.path.chmod(0o644)
        self.package.state.initialize()
        self.assertEqual(before, self.package.state.path.read_bytes())
        self.assertEqual(stat.S_IMODE(self.state.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(self.package.state.path.stat().st_mode), 0o600)

    def test_external_cannot_shadow_managed_or_scan_all(self):
        with self.assertRaisesRegex(sonar.PackageError, "shadow"):
            self.package.names("app")
        for name, all_projects in ((None, True), ("external", True), (None, False)):
            with self.subTest(name=name, all_projects=all_projects), self.assertRaises(sonar.PackageError):
                self.package.names(name, all_projects)

    def test_external_mount_and_host_identity_must_be_valid(self):
        alias = self.root / "alias"
        alias.symlink_to(self.source, target_is_directory=True)
        for path, host in ((self.root / "missing", str(self.source)), (alias, str(self.source)),
                           (Path("relative"), str(self.source)), (self.source, None), (self.source, "relative")):
            with self.subTest(path=path, host=host), self.assertRaises(sonar.PackageError):
                self.package.select_external(path, host)

    def test_source_binding_survives_registration_interruption(self):
        self.package.state.initialize()
        with patch.object(self.package, "client") as client:
            client.return_value.request.side_effect = sonar.APIError("unreachable")
            with self.assertRaises(sonar.APIError):
                self.package.ensure_project("external")
        restored = sonar.State(self.state)
        self.assertEqual(restored.data["projects"]["external"]["source_path"], str(self.source))
        self.assertEqual(restored.data["schema_version"], 1)
        self.package.select_external(self.source, str(self.root / "different checkout"))
        with self.assertRaisesRegex(sonar.PackageError, "different source"):
            self.package.names("external")
        managed = sonar.SonarPackage(self.state, self.projects, self.config)
        with self.assertRaisesRegex(sonar.PackageError, "different source"):
            managed.ensure_project("external")

    def test_existing_managed_history_cannot_be_adopted(self):
        self.package.state.initialize()
        self.package.state.data["projects"]["external"] = {
            "key": "local:external", "token_name": "legacy", "token": "legacy-token"}
        self.package.state.save()
        with self.assertRaisesRegex(sonar.PackageError, "different source"):
            self.package.validate(["external"])

    def test_external_token_recovery_preserves_binding(self):
        api = fixtures.FakeSonar()
        self.addCleanup(api.close)
        self.package.api_url = api.url
        self.package.state.initialize()
        with redirect_stdout(io.StringIO()):
            self.package.bootstrap()
        first = self.package.ensure_project("external")
        self.assertEqual(self.package.ensure_project("external"), first)
        api.tokens[first[1]]["valid"] = False
        self.assertNotEqual(self.package.ensure_project("external")[1], first[1])
        self.assertEqual(sonar.State(self.state).data["projects"]["external"]["source_path"], str(self.source))

    def test_scanner_gets_external_checkout_and_writes_only_state(self):
        before = {p.relative_to(self.source): p.read_bytes() for p in self.source.rglob("*") if p.is_file()}
        observed = []

        def scanner(repository, settings, token):
            observed.append(repository)
            work = Path(sonar.read_properties(settings)["sonar.working.directory"])
            work.mkdir()
            (work / "report-task.txt").write_text("ceTaskId=task\n")

        with patch.object(self.package, "ensure_project", return_value=("local:external", "secret")), \
                patch.object(self.package, "run_scanner", side_effect=scanner), \
                patch.object(self.package, "wait_analysis", return_value="OK"), redirect_stdout(io.StringIO()):
            self.assertEqual(self.package.scan_one("external"), "OK")
        self.assertEqual(observed, [self.source])
        result = json.loads(next((self.state / "scans").glob("external/*/result.json")).read_text())
        self.assertEqual(result["source_path"], str(self.source))
        self.assertEqual(before, {p.relative_to(self.source): p.read_bytes() for p in self.source.rglob("*") if p.is_file()})

    def test_corrupt_source_binding_is_rejected(self):
        self.package.state.initialize()
        for value in (None, 123, "relative"):
            self.package.state.data["projects"]["external"] = {
                "key": "local:external", "token_name": "", "token": "", "source_path": value}
            self.package.state.save()
            with self.assertRaisesRegex(sonar.PackageError, "source binding"):
                sonar.State(self.state)

    def test_report_does_not_require_source_or_change_credentials(self):
        self.package.state.initialize()
        self.package.state.data["admin_initialized"] = True
        self.package.state.save()
        before = self.package.state.path.read_bytes()
        shutil.rmtree(self.source)
        package = sonar.SonarPackage(self.state, self.projects, self.config)
        output = io.StringIO()
        with patch.object(package, "client", return_value=test_report.FakeClient()), redirect_stdout(output):
            package.report("external")
        exported = next((self.state / "reports" / "external").glob("*/index.html"))
        self.assertTrue((exported.parent / "issues.csv").is_file())
        self.assertTrue((exported.parent / "security-hotspots.csv").is_file())
        self.assertEqual(before, self.package.state.path.read_bytes())
        self.assertNotIn(package.state.data["admin_password"], exported.read_text())
        self.assertIn("Report:", output.getvalue())


class ExternalWrapperTests(unittest.TestCase):
    setUp = fixtures.WrapperTests.setUp
    tearDown = fixtures.WrapperTests.tearDown
    cli = fixtures.WrapperTests.cli

    def test_external_path_is_canonical_and_never_forwarded_as_container_path(self):
        source = self.root / "checkout with spaces"
        source.mkdir()
        alias = self.root / "link"
        alias.symlink_to(source, target_is_directory=True)
        for engine in ("docker", "podman"):
            result = self.cli("--engine", engine, "scan", "external", "--path", str(alias), "--check-gate")
            self.assertEqual(result.returncode, 0, result.stderr)
            calls = (self.calls if engine == "docker" else self.podman_calls).read_text()
            self.assertIn("compose.external.yaml", calls)
            self.assertIn("tools validate external --path /external-project --check-gate", calls)
            self.assertIn("tools scan external --path /external-project --check-gate", calls)
            self.assertNotIn("--path " + str(alias), calls)

    def test_invalid_external_arguments_fail_before_engine_calls(self):
        source = self.root / "checkout"
        source.mkdir()
        for args in (("scan",), ("scan", "--all", "--path", str(source)), ("scan", "app", "--path"),
                     ("scan", "app", "--path", ""), ("scan", "--path", str(source)),
                     ("scan", "app", "--path", str(source / "missing")),
                     ("scan", "app", "--path", str(source), "--path", str(source)),
                     ("scan", "../app", "--path", str(source))):
            with self.subTest(args=args):
                self.assertEqual(self.cli(*args).returncode, 1)
                self.assertFalse(self.calls.exists())
                self.assertFalse((self.root / ".state").exists())

    def test_external_name_cannot_shadow_a_managed_folder(self):
        (self.root / "projects" / "app").mkdir(parents=True)
        source = self.root / "outside"
        source.mkdir()
        result = self.cli("scan", "app", "--path", str(source))
        self.assertEqual(result.returncode, 1)
        self.assertIn("shadow", result.stderr)
        self.assertFalse(self.calls.exists())

    def test_provider_without_project_directory_option_runs_from_package_root(self):
        binary = self.binary / "podman"
        binary.write_text(binary.read_text().replace(
            '#!/usr/bin/env bash',
            '#!/usr/bin/env bash\n'
            'case "$*" in *--project-directory*) exit 97 ;; esac\n'
            'if [ "$1" = compose ]; then printf "cwd=%s\\n" "$PWD" >> "$PODMAN_CALLS"; fi', 1))
        result = self.cli("--engine", "podman", "down")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("cwd=" + str(self.root.resolve()), self.podman_calls.read_text())

    def test_status_lists_stopped_containers_without_provider_specific_flags(self):
        binary = self.binary / "podman"
        binary.write_text(binary.read_text().replace(
            '#!/usr/bin/env bash',
            '#!/usr/bin/env bash\ncase "$*" in *"ps -a"*) exit 97 ;; esac', 1))
        result = self.cli("--engine", "podman", "status")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("ps --all --filter label=com.docker.compose.project=sonar-package-", self.podman_calls.read_text())

    def test_external_scan_selects_empty_projects_mount_without_inherited_override(self):
        binary = self.binary / "podman"
        binary.write_text(binary.read_text().replace(
            '#!/usr/bin/env bash',
            '#!/usr/bin/env bash\n'
            'if [ "$1" = compose ]; then printf "projects_source=%s\\n" "${SONAR_PROJECTS_PATH:-}" >> "$PODMAN_CALLS"; fi', 1))
        source = self.root / "outside"
        source.mkdir()
        self.environment["SONAR_PROJECTS_PATH"] = "/unrelated/inherited/path"
        result = self.cli("--engine", "podman", "scan", "external", "--path", str(source))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("projects_source=" + str(self.root.resolve() / ".state/podman/empty-projects"), self.podman_calls.read_text())
        self.podman_calls.unlink()
        result = self.cli("--engine", "podman", "scan", "app")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("projects_source=" + str(self.root.resolve() / "projects"), self.podman_calls.read_text())
        self.assertNotIn("/unrelated/inherited/path", self.podman_calls.read_text())


if __name__ == "__main__":
    unittest.main()
