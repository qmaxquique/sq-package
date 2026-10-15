# Targeted recovery

Use the selected engine and remembered port throughout. Read the failing output and relevant package logs, make the smallest applicable correction, then retry that step. Do not cycle through identical failures or delete history to get a clean setup.

| Observed problem | Response |
| --- | --- |
| Engine unavailable | Start the selected installed daemon/VM; install only if missing. Never silently fall back to the other engine. |
| VM reports started but stops as the launching tool session exits | Retry from a persistent terminal session and verify `podman info` from another command. Keep that session available if its lifetime controls the VM, or start through the user's native terminal. Do not reinitialize the machine or change its resources to address a launch-session problem. |
| Remote endpoint rejected | Inspect current context/connection and select a supported local one for this operation. Do not globally replace unrelated defaults. |
| Podman provider missing | Verify `podman compose version`; install/select a provider. A Docker Compose provider on native Linux may need the Podman user API socket. |
| Kernel preflight below minimum | Raise only the insufficient sysctl on the actual Linux host/VM, using setup instructions. Never disable bootstrap checks. |
| Port occupied | Identify whether it is this stack or another service; select and remember an alternate port without stopping unrelated services. |
| Native path not supported | The package version is incompatible. Identify the checkout/version and explain how to obtain a compatible release; do not silently pull/reset it. |
| Source binding/name collision | Use the name generated for the intended canonical checkout; choose a new logical name if necessary. Do not edit binding state to take over existing history. |
| Source/config permission failure | Check host user access and VM sharing. The tools container runs as the invoking user. Do not recursively chown or SELinux-relabel repositories. |
| Rootless Podman cannot write `/state` although host access passes | Compare numeric ownership inside the selected VM with its user's UID/GID; macOS sharing can expose different ownership. Use `podman machine ssh "$MACHINE" stat -c %u:%g:%a "$PACKAGE_ROOT/.state/podman"` and VM `id`. If only this generated private state directory is root-owned, correct that exact directory to the VM user's UID/GID with VM `sudo chown`, then retry. Do not recurse over the package or source, broaden permissions, or switch rootful mode. Inspect any affected state files separately without printing their contents. |
| No selected PHP/Python sources | Check actual source roots, suffixes, inclusions, test separation, and exclusions. Replace WordPress template placeholders; retain dependency exclusions. |
| Coverage/test file missing | For ordinary analysis, disclose the invalid configured import and correct configuration to match verified available reports. Generate reports only if requested; don't silently remove an intentional coverage requirement. |
| XML uses host paths | Regenerate portable paths in the existing test environment when requested; source/config paths must match the scanner mount. Do not fabricate coverage data. |
| Active package lock | Report owner PID and let the current operation finish. Do not delete a live lock. Dead-owner locks recover automatically. |
| Ownerless lock | Confirm no package command remains, then remove only the stale lock directory. |
| Startup timeout | Inspect `logs sonarqube`/`logs postgres`, VM resources, kernel limits and credential pairing. Increase `SONAR_STARTUP_TIMEOUT` only when justified. |
| Analysis timeout | Results can complete later. Inspect task/dashboard before rescanning; increase `SONAR_ANALYSIS_TIMEOUT` for future work if appropriate. |
| Scanner timeout | Check selected scope and resources; increase `SONAR_SCAN_TIMEOUT` for a justified larger repository. |
| Gate failed | Report completed publication and actual failing conditions. Do not weaken the gate or exclude problematic code to claim success. |
| Revoked/expired/orphaned project token | Retry the scan: package registration replaces invalid tokens and recovers deterministic orphaned tokens automatically. |
| Changed admin password | Restore matching state or update only the saved admin password to the actual one, preserving file permissions. Do not print secrets or rotate/reset blindly. |
| Missing/corrupt credentials with existing DB | Preserve state/volumes and restore a matching backup. Explain the mismatch; reset requires an explicit deletion request. |
| Export count/analysis changed | Wait until analysis/review edits finish, retry report once, then report a recurring blocker. Never deliver a silently truncated export. |
| Registry credential-helper failure | Inspect the selected engine's helper/proxy configuration without exposing auth. If appropriate use an operation-scoped empty auth file for public images, as in the README; preserve existing credentials. |

`down` preserves credentials, volumes and exports. `reset --yes` removes selected-engine volumes and state, including reports; repositories/config and the other engine remain. A backup/move must keep matching state and volumes together. Do not stop services after a scan unless asked: dashboard links depend on them.
