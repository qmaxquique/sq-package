# Prerequisites and setup

Use the discovered package's README and pinned image/tool versions as the package contract. Check the linked official installation documentation before installing tools: installer/package-manager details can change. Do not install host PHP, Composer, PHPUnit, Java, Python, or pytest merely to run the package. The helper scripts need Bash and a SHA-256 tool (`sha256sum`, `shasum`, or `openssl`) in addition to Git and the engine.

## Select and remember

When no explicit request, `SONAR_ENGINE`, or saved choice exists, ask which engine to use. On this machine prefer presenting installed Podman first, without silently selecting it. On other machines present available tools as context, not a hardcoded preference.

Create/update `$PACKAGE_ROOT/config/sq.local.properties` with literal known values, preserving the other engine's port:

```properties
engine=podman
podman.port=9000
```

Values: engine `docker`/`podman`, ports integers `1..65535`. Reject malformed/duplicate known settings; never execute this file as shell. The skill reads these preferences and supplies explicit wrapper arguments; the wrapper itself retains its Docker default. A reset preserves this configuration file. Choose another free port if needed and retain it for later commands. Do not redirect a previously initialized stack without identifying its current engine and port.

## Required versus recommended

| Check | Requirement/action |
| --- | --- |
| Platform | macOS or Linux, AMD64 or ARM64; Windows and arbitrary remote engines are unsupported. |
| Tools | Bash, Git, selected engine, working Compose implementation. |
| Locality | Docker Unix socket; native Podman or a recognized local Podman machine. Preserve selected contexts/connections. |
| Kernel | Linux container host/VM: `vm.max_map_count >= 524288`, `fs.file-max >= 131072`. |
| Limits | Compose supplies SonarQube `nofile=131072`, `nproc=8192`; do not disable bootstrap checks. |
| Resources | Recommend 8 GB RAM, 2 CPUs, 30 GB disk and at least 10% free in the engine. Low allocations are recommendations, not automatic hard failures. |
| Files | Package state/config writable by user; original source readable and shared with the engine VM. Source/config container mounts stay read-only. |
| Network | Public image registries, SonarSource scanner distribution, and Ubuntu package repositories during the first tools build. Respect existing proxy/registry settings. |

## macOS

**Podman:** follow [installation](https://podman.io/docs/installation). Reuse an existing installation; if Homebrew is already the selected system package manager, `brew install podman podman-compose` installs the engine/provider. Do not install a second package manager as a shortcut. Inspect `podman machine list`, `podman system connection list`, and selected `CONTAINER_CONNECTION`/`CONTAINER_HOST` before choosing a machine.

For a missing machine:

```bash
podman machine init --cpus 2 --memory 8192 --disk-size 30
podman machine start
```

Start an existing selected machine by its actual name. Do not recreate it or change its resources by stopping other workloads implicitly. Ensure both package and source paths are VM-shared; home directories are normally shared. For custom paths, inspect the selected machine's mounts and follow [machine initialization options](https://docs.podman.io/en/stable/markdown/podman-machine-init.1.html). Do not remove/recreate a machine to repair sharing.

Run `podman compose version`. Respect `PODMAN_COMPOSE_PROVIDER`; an external `docker-compose` provider does not require a Docker daemon. If needed select an installed provider explicitly for package commands. Kernel checks/changes belong inside the **selected VM**:

```bash
podman machine ssh "$MACHINE" sysctl vm.max_map_count fs.file-max
# Only if below the relevant minimum:
podman machine ssh "$MACHINE" sudo sysctl -w vm.max_map_count=524288
podman machine ssh "$MACHINE" sudo sysctl -w fs.file-max=131072
```

**Docker:** use [Docker Desktop for Mac](https://docs.docker.com/desktop/setup/install/mac-install/) for the native architecture. Reuse an installed application; install through the existing supported package manager or official installer when missing, then start it and wait for `docker info`. Interactive installer/login/license actions must be completed by the user when required; report the exact step. Verify `docker compose version` and local context. Allocate resources and share source/package directories through supported Desktop settings.

Docker kernel settings belong inside its Linux VM. Run `./sonar --engine docker up`: it builds tools and runs the authoritative read-only preflight before starting services. If it reports insufficient limits, use the resulting tools image:

```bash
TOOLS_IMAGE="$(cat "$PACKAGE_ROOT/.state/stack-name")-tools"
docker run --rm --read-only --entrypoint python3 "$TOOLS_IMAGE" \
  -c 'from pathlib import Path; print(Path("/proc/sys/vm/max_map_count").read_text()); print(Path("/proc/sys/fs/file-max").read_text())'
```

Record the values. Where existing permissions allow the required VM adjustment, use the package README's temporary privileged-container procedure, setting only insufficient limits. For example:

```bash
docker run --rm --privileged --user 0:0 --entrypoint python3 "$TOOLS_IMAGE" \
  -c 'from pathlib import Path; Path("/proc/sys/vm/max_map_count").write_text("524288\n")'
```

Apply the analogous `/proc/sys/fs/file-max` write only if below its minimum. Never run these writes against macOS sysctl. Docker Desktop VM restarts/Resource Saver can reset values: recheck after restart rather than promising persistence.

## Linux

Use [Docker Engine installation](https://docs.docker.com/engine/install/) for the detected distribution plus its Compose plugin, or [Podman installation](https://podman.io/docs/installation) plus a Compose provider. Use supported distribution/vendor repositories and the existing package manager; do not run an unreviewed curl-to-shell installer. Start the selected existing service/socket as appropriate; verify `docker info`/`podman info` and Compose.

With native rootless Podman and a Docker Compose provider, the user API socket may need `systemctl --user start podman.socket`. A `podman-compose` provider calls Podman directly. Do not switch rootless/rootful mode, globally redirect contexts, or recursively chown/relabel sources to bypass access errors. The package already handles UID/GID, rootless keep-id, and tools-only SELinux settings.

Inspect `sysctl vm.max_map_count fs.file-max` on the actual Linux container host. Set only insufficient values with `sudo sysctl -w`. For persistent native-host or Podman-VM setup, manage only `/etc/sysctl.d/99-sonarqube.conf`, containing the required values, and apply that file with `sudo sysctl -p /etc/sysctl.d/99-sonarqube.conf`. Preserve other settings/files, never lower a higher effective value, and report any pre-existing managed-file conflict before replacing unrelated content.

## Finish setup

Run diagnostics again, then `SONAR_PORT="$PORT" ./sonar --engine "$ENGINE" up` from the package directory. Package preflight must pass; readiness/bootstrap must complete. Then run the requested scan: its tools-container validation verifies actual source access. If administrative/interactive access is unavailable, provide the exact failing action and retain progress. Never present unverified kernel or VM sharing checks as passed.
