import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "codex" / "sq" / "scripts"


class DoctorTests(unittest.TestCase):
    scripts = SCRIPTS

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="sq doctor ")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.package = self.root / "package"
        (self.package / "config" / "projects").mkdir(parents=True)
        (self.package / "scripts").mkdir()
        for name in ("sonar", "compose.external.yaml", "scripts/sonar.py"):
            (self.package / name).write_text("fixture")
        (self.package / "sonar").chmod(0o755)
        self.source = self.root / "outside source"
        self.source.mkdir()
        self.binary = self.root / "bin"
        self.binary.mkdir()
        self.calls = self.root / "calls"
        self.environment = dict(os.environ, SQ_TEST_CALLS=str(self.calls))
        for name in ("DOCKER_HOST", "DOCKER_CONTEXT", "CONTAINER_HOST", "CONTAINER_CONNECTION", "SONAR_PORT"):
            self.environment.pop(name, None)
        self.environment["PATH"] = str(self.binary) + os.pathsep + os.environ["PATH"]
        engine = '''#!/usr/bin/env bash
printf '%s\n' "$*" >> "$SQ_TEST_CALLS"
if [ "$1" = info ]; then
    if [ "${2:-}" = --format ]; then
        case "$3" in
            *ServiceIsRemote*) printf '%s\n' "${SQ_TEST_REMOTE:-false}" ;;
            *CPUs*|*NCPU*) printf '%s\n' "${SQ_TEST_CPUS:-2}" ;;
            *MemTotal*) printf '%s\n' "${SQ_TEST_MEMORY:-8589934592}" ;;
        esac
    fi
    printf '%s' "${SQ_TEST_SECRET:-}" >&2
    exit "${SQ_TEST_DOWN:-0}"
fi
if [ "$1" = context ]; then printf '%s\n' "${SQ_TEST_ENDPOINT:-unix:///local.sock}"; fi
if [ "$1" = system ]; then printf '%b' "${SQ_TEST_CONNECTIONS:-}"; fi
if [ "$1" = ps ]; then printf '%s' "${SQ_TEST_PORTS:-}"; fi
if [ "$1" = compose ]; then
    printf '%s' "${SQ_TEST_SECRET:-}" >&2
    exit "${SQ_TEST_COMPOSE_DOWN:-0}"
fi
'''
        for name in ("docker", "podman"):
            self.executable(name, engine)
        self.executable("uname", '''#!/usr/bin/env bash
if [ "$1" = -s ]; then printf '%s\n' "${SQ_TEST_OS:-Linux}"; else printf '%s\n' x86_64; fi
''')
        self.executable("sysctl", '''#!/usr/bin/env bash
case "$2" in vm.max_map_count) printf '%s\n' "${SQ_TEST_MAPS:-524288}" ;; *) printf '%s\n' "${SQ_TEST_FILES:-131072}" ;; esac
''')
        self.executable("lsof", '#!/usr/bin/env bash\nexit "${SQ_TEST_BUSY:-1}"\n')

    def executable(self, name, content):
        path = self.binary / name
        path.write_text(content)
        path.chmod(0o755)

    def run_doctor(self, engine="podman", source=True):
        command = ["bash", str(self.scripts / "doctor.sh"), "--package", str(self.package), "--engine", engine]
        if source:
            command += ["--source", str(self.source)]
        return subprocess.run(command, env=self.environment, text=True, capture_output=True, timeout=10)

    def test_native_checks_pass_without_writing_package_or_source(self):
        before = {p.relative_to(self.package): p.read_bytes() for p in self.package.rglob("*") if p.is_file()}
        result = self.run_doctor()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(before, {p.relative_to(self.package): p.read_bytes() for p in self.package.rglob("*") if p.is_file()})
        self.assertEqual(list(self.source.iterdir()), [])
        for call in self.calls.read_text().splitlines():
            self.assertNotIn(call.split()[0], {"build", "pull", "run", "exec", "start", "machine"})

    def test_stopped_engine_and_provider_errors_do_not_expose_output(self):
        self.environment["SQ_TEST_SECRET"] = "DO_NOT_PRINT"
        self.environment["SQ_TEST_DOWN"] = "125"
        result = self.run_doctor()
        self.assertEqual(result.returncode, 1)
        self.assertNotIn("DO_NOT_PRINT", result.stdout + result.stderr)
        self.environment["SQ_TEST_DOWN"] = "0"
        self.environment["SQ_TEST_COMPOSE_DOWN"] = "1"
        result = self.run_doctor()
        self.assertEqual(result.returncode, 1)
        self.assertIn("Compose provider", result.stdout)
        self.assertNotIn("DO_NOT_PRINT", result.stdout + result.stderr)

    def test_missing_engine_is_a_blocker(self):
        (self.binary / "podman").unlink()
        # Isolate PATH so a CI runner's real installed engine cannot be invoked.
        for name in ("bash", "git"):
            (self.binary / name).symlink_to(shutil.which(name))
        self.environment["PATH"] = str(self.binary)
        result = self.run_doctor()
        self.assertEqual(result.returncode, 1)
        self.assertIn("Install the selected engine", result.stdout)

    def test_remote_connections_are_blocked(self):
        self.environment["DOCKER_HOST"] = "ssh://private-secret@remote.example"
        result = self.run_doctor("docker")
        self.assertEqual(result.returncode, 1)
        self.assertNotIn("private-secret", result.stdout + result.stderr)
        self.environment["CONTAINER_HOST"] = "tcp://remote.example:1234"
        result = self.run_doctor()
        self.assertEqual(result.returncode, 1)
        self.assertIn("Remote Podman", result.stdout)

    def test_selected_remote_connection_cannot_use_default_local_connection(self):
        self.environment.update(CONTAINER_CONNECTION="remote", SQ_TEST_REMOTE="true",
                                SQ_TEST_CONNECTIONS="local\\tunix:///local.sock\\ttrue\\tfalse\\nremote\\tssh://user@elsewhere/run/podman.sock\\tfalse\\tfalse\\n")
        self.assertEqual(self.run_doctor().returncode, 1)

    def test_native_podman_ignores_unused_saved_connections(self):
        self.environment["SQ_TEST_CONNECTIONS"] = "remote\\tssh://user@elsewhere/run/podman.sock\\ttrue\\tfalse\\n"
        self.assertEqual(self.run_doctor().returncode, 0)
        self.assertNotIn("connection list", self.calls.read_text())

    def test_local_machine_leaves_kernel_and_sharing_unverified(self):
        self.environment.update(SQ_TEST_OS="Darwin", SQ_TEST_REMOTE="true",
                                SQ_TEST_CONNECTIONS="machine\\tssh://core@127.0.0.1:1234/run/podman.sock\\ttrue\\ttrue\\n")
        result = self.run_doctor()
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        self.assertIn("Engine endpoint is local", result.stdout)
        self.assertIn("[UNVERIFIED] Container kernel", result.stdout)
        self.assertIn("VM source sharing", result.stdout)

    def test_docker_socket_does_not_imply_host_kernel(self):
        self.environment["SQ_TEST_MAPS"] = "1"
        result = self.run_doctor("docker", source=False)
        self.assertEqual(result.returncode, 3)
        self.assertNotIn("Raise vm.max_map_count", result.stdout)

    def test_low_limits_block_and_unreadable_limits_remain_unverified(self):
        self.environment["SQ_TEST_MAPS"] = "1000"
        self.assertEqual(self.run_doctor().returncode, 1)
        self.environment["SQ_TEST_MAPS"] = "unavailable"
        self.assertEqual(self.run_doctor().returncode, 3)
        self.environment["SQ_TEST_MAPS"] = "524288"
        self.environment["SQ_TEST_FILES"] = "1000"
        self.assertEqual(self.run_doctor().returncode, 1)

    def test_resource_recommendations_are_not_mandatory(self):
        self.environment.update(SQ_TEST_CPUS="1", SQ_TEST_MEMORY="1000000")
        result = self.run_doctor()
        self.assertEqual(result.returncode, 0)
        self.assertIn("[RECOMMENDATION] Allocate 8 GB", result.stdout)

    def test_occupied_port_and_own_existing_stack(self):
        self.environment["SQ_TEST_BUSY"] = "0"
        self.assertEqual(self.run_doctor().returncode, 1)
        state = self.package / ".state" / "podman"
        state.mkdir(parents=True)
        (state / "stack-name").write_text("sonar-test\n")
        (state / "credentials.json").write_text("DO_NOT_PRINT")
        self.environment["SQ_TEST_PORTS"] = "127.0.0.1:9000->9000/tcp"
        result = self.run_doctor()
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertNotIn("DO_NOT_PRINT", result.stdout + result.stderr)

    def test_missing_source_and_invalid_port(self):
        self.source.rmdir()
        self.assertEqual(self.run_doctor().returncode, 1)
        self.environment["SONAR_PORT"] = "99999999999999"
        self.assertEqual(self.run_doctor().returncode, 2)


class ProjectNameTests(unittest.TestCase):
    scripts = SCRIPTS

    def test_name_is_stable_canonical_and_distinguishes_checkouts(self):
        with tempfile.TemporaryDirectory(prefix="sq identity ") as temporary:
            root = Path(temporary).resolve()
            first = root / "one" / "same name"
            second = root / "two" / "same name"
            first.mkdir(parents=True)
            second.mkdir(parents=True)
            alias = root / "alias"
            alias.symlink_to(first, target_is_directory=True)

            def name(path):
                return subprocess.check_output(["bash", str(self.scripts / "project-name.sh"), str(path)], text=True).strip()

            expected = "ext-same-name-" + hashlib.sha256(str(first).encode()).hexdigest()[:12]
            self.assertEqual(name(first), expected)
            self.assertEqual(name(alias), expected)
            self.assertNotEqual(name(first), name(second))
            self.assertRegex(name(first), r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,199}$")


class CopilotDoctorTests(DoctorTests):
    scripts = ROOT / "skills" / "copilot" / "sq" / "scripts"


class CopilotProjectNameTests(ProjectNameTests):
    scripts = ROOT / "skills" / "copilot" / "sq" / "scripts"


class ClaudeDoctorTests(DoctorTests):
    scripts = ROOT / "skills" / "claude" / "sq" / "scripts"


class ClaudeProjectNameTests(ProjectNameTests):
    scripts = ROOT / "skills" / "claude" / "sq" / "scripts"


if __name__ == "__main__":
    unittest.main()
