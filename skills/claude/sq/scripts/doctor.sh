#!/usr/bin/env bash
# Read-only host diagnostics. Never build/pull/start engines, containers or services.
# Exit: 0 checked, 1 blockers, 2 usage, 3 mandatory checks remain unverified.
set -euo pipefail
PACKAGE=''
ENGINE=''
SOURCE=''
PORT=${SONAR_PORT:-9000}
BLOCKERS=0
UNKNOWN=0
usage() { printf 'Usage: doctor.sh --package ROOT --engine docker|podman [--source PATH] [--port PORT]\n'; }
while [ "$#" -gt 0 ]; do
    case "$1" in
        --package|--engine|--source|--port)
            [ "$#" -ge 2 ] || { usage >&2; exit 2; }
            case "$1" in --package) PACKAGE=$2 ;; --engine) ENGINE=$2 ;; --source) SOURCE=$2 ;; --port) PORT=$2 ;; esac
            shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) usage >&2; exit 2 ;;
    esac
done
case "$ENGINE" in docker|podman) ;; *) usage >&2; exit 2 ;; esac
case "$PORT" in ''|*[!0-9]*) usage >&2; exit 2 ;; esac
[ "${#PORT}" -le 5 ] && [ "$PORT" -ge 1 ] && [ "$PORT" -le 65535 ] || { usage >&2; exit 2; }
[ -n "$PACKAGE" ] || { usage >&2; exit 2; }
ok() { printf '[OK] %s\n' "$*"; }
block() { printf '[BLOCKER] %s\n' "$*"; BLOCKERS=$((BLOCKERS + 1)); }
unknown() { printf '[UNVERIFIED] %s\n' "$*"; UNKNOWN=$((UNKNOWN + 1)); }
recommend() { printf '[RECOMMENDATION] %s\n' "$*"; }
numeric() { case "$1" in ''|*[!0-9]*) return 1 ;; esac; }

OS=$(uname -s)
ARCH=$(uname -m)
case "$OS" in Darwin|Linux) ok 'Supported operating system.' ;; *) block 'Use macOS or Linux.' ;; esac
case "$ARCH" in arm64|aarch64|x86_64|amd64) ok 'Supported architecture.' ;; *) block 'Use ARM64 or AMD64.' ;; esac
if command -v git >/dev/null 2>&1; then ok 'Git is installed.'; else block 'Install Git.'; fi
if [ -x "$PACKAGE/sonar" ] && [ -f "$PACKAGE/compose.external.yaml" ] && [ -f "$PACKAGE/scripts/sonar.py" ]; then
    PACKAGE=$(cd -- "$PACKAGE" && pwd -P)
    ok 'Package supports external checkouts.'
else
    block 'Select a compatible sq-package checkout with executable sonar and compose.external.yaml.'
fi
if [ ! -d "$PACKAGE/config/projects" ] || [ ! -w "$PACKAGE/config/projects" ]; then
    block 'Package project configuration directory must exist and be writable.'
fi
if [ -d "$PACKAGE/.state" ] && [ ! -w "$PACKAGE/.state" ]; then
    block 'Package state must be writable by the invoking user.'
elif [ ! -e "$PACKAGE/.state" ] && [ ! -w "$PACKAGE" ]; then
    block 'Package root must be writable to create state.'
fi
if [ -L "$PACKAGE/.state" ]; then block 'Package .state must not be a symbolic link.'; fi
if [ -n "$SOURCE" ]; then
    if [ -d "$SOURCE" ] && [ -r "$SOURCE" ] && [ -x "$SOURCE" ]; then
        ok 'Source directory is readable on the host.'
    else
        block 'Select an existing readable source directory.'
    fi
fi

LOCAL=false
NATIVE=false
RUNNING=false
if ! command -v "$ENGINE" >/dev/null 2>&1; then
    block 'Install the selected engine and its Compose support (see setup reference).'
elif ! "$ENGINE" info >/dev/null 2>&1; then
    block 'Start the selected engine or its local VM; inspect its info command.'
else
    RUNNING=true
    ok 'Selected engine is running.'
    if [ "$ENGINE" = docker ]; then
        endpoint=${DOCKER_HOST:-}
        if [ -z "$endpoint" ]; then endpoint=$(docker context inspect --format '{{.Endpoints.docker.Host}}' 2>/dev/null || true); fi
        case "$endpoint" in unix://*) LOCAL=true ;; *) block 'Select a local Docker Unix-socket context.' ;; esac
        if ! docker compose version >/dev/null 2>&1; then block 'Install the Docker Compose plugin.'; else ok 'Compose is available.'; fi
        cpus=$(docker info --format '{{.NCPU}}' 2>/dev/null || true)
        memory=$(docker info --format '{{.MemTotal}}' 2>/dev/null || true)
    else
        remote=$(podman info --format '{{.Host.ServiceIsRemote}}' 2>/dev/null || true)
        endpoint=${CONTAINER_HOST:-}
        machine=false
        connections=''
        if [ "$remote" = true ] || [ -n "${CONTAINER_HOST:-}${CONTAINER_CONNECTION:-}" ]; then
            connections=$(podman system connection list --format '{{.Name}}\t{{.URI}}\t{{.Default}}\t{{.IsMachine}}' 2>/dev/null || true)
        fi
        while IFS=$'\t' read -r name uri default is_machine; do
            if [ -n "${CONTAINER_HOST:-}" ]; then
                if [ "$uri" = "$CONTAINER_HOST" ]; then machine=$is_machine; fi
            elif { [ -n "${CONTAINER_CONNECTION:-}" ] && [ "$name" = "$CONTAINER_CONNECTION" ]; } ||
                 { [ -z "${CONTAINER_CONNECTION:-}" ] && [ "$default" = true ]; }; then
                endpoint=$uri
                machine=$is_machine
            fi
        done <<< "$connections"
        if [ -n "${CONTAINER_CONNECTION:-}" ] && [ -z "$endpoint" ]; then
            block 'Cannot resolve the selected Podman connection.'
        else
            case "$endpoint" in
                unix://*) LOCAL=true ;;
                '') if [ "$remote" = false ]; then LOCAL=true; NATIVE=true; else block 'Cannot verify the Podman endpoint.'; fi ;;
                ssh://*@127.0.0.1:*|ssh://*@localhost:*) if [ "$machine" = true ]; then LOCAL=true; else block 'Only a known local Podman machine is supported.'; fi ;;
                *) block 'Remote Podman endpoints are unsupported.' ;;
            esac
        fi
        if [ "$remote" = false ] && [ "$LOCAL" = true ]; then NATIVE=true; fi
        if ! (unset DOCKER_HOST DOCKER_CONTEXT; podman compose version >/dev/null 2>&1); then
            block 'Install a Podman Compose provider; check PODMAN_COMPOSE_PROVIDER and socket availability.'
        else ok 'Compose provider is available.'; fi
        cpus=$(podman info --format '{{.Host.CPUs}}' 2>/dev/null || true)
        memory=$(podman info --format '{{.Host.MemTotal}}' 2>/dev/null || true)
    fi
    if [ "$LOCAL" = true ]; then ok 'Engine endpoint is local.'; fi
    if numeric "$cpus"; then
        if [ "$cpus" -lt 2 ]; then recommend 'Allocate at least 2 CPUs to the engine.'; else ok 'Engine has at least 2 CPUs.'; fi
    else printf '[UNVERIFIED] Engine CPU allocation.\n'; fi
    if numeric "$memory"; then
        if [ "$memory" -lt 8589934592 ]; then recommend 'Allocate 8 GB RAM to the engine.'; else ok 'Engine has at least 8 GB RAM.'; fi
    else printf '[UNVERIFIED] Engine memory allocation.\n'; fi
fi

# Only native Linux Podman is known to share this host's sysctls. A Docker
# Unix socket can belong to a VM even on Linux: never inspect the wrong kernel.
if [ "$OS" = Linux ] && [ "$NATIVE" = true ]; then
    for entry in vm.max_map_count:524288 fs.file-max:131072; do
        key=${entry%%:*}
        minimum=${entry#*:}
        value=$(sysctl -n "$key" 2>/dev/null || true)
        if ! numeric "$value"; then unknown "Cannot inspect $key; run package preflight.";
        elif [ "$value" -lt "$minimum" ]; then block "Raise $key to at least $minimum on the Linux container host.";
        else ok "$key meets the required minimum."; fi
    done
else
    unknown 'Container kernel limits: use the VM procedure in setup reference; package preflight is authoritative.'
fi
if [ -n "$SOURCE" ] && [ "$NATIVE" != true ]; then
    unknown 'VM source sharing and container read access: verify the bind mount during package validation.'
fi

owned_port=false
state="$PACKAGE/.state"
if [ "$ENGINE" = podman ]; then state="$state/podman"; fi
if [ "$RUNNING" = true ] && [ "$LOCAL" = true ] && [ -f "$state/stack-name" ] && [ ! -L "$state/stack-name" ]; then
    stack=''
    IFS= read -r stack < "$state/stack-name" || true
    case "$stack" in ''|*[!a-z0-9_-]*) ;; *)
        ports=$("$ENGINE" ps --filter "label=com.docker.compose.project=$stack" --filter 'label=com.docker.compose.service=sonarqube' --format '{{.Ports}}' 2>/dev/null || true)
        case "$ports" in *"127.0.0.1:$PORT->9000"*) owned_port=true ;; esac ;;
    esac
fi
if [ "$owned_port" = true ]; then
    ok 'Selected port belongs to this package stack.'
elif command -v lsof >/dev/null 2>&1; then
    if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN -t >/dev/null 2>&1; then block 'Selected port is occupied; choose and remember another SONAR_PORT.';
    else ok 'No listener detected on the selected port.'; fi
elif command -v ss >/dev/null 2>&1; then
    listeners=$(ss -H -ltn "sport = :$PORT" 2>/dev/null || true)
    if [ -n "$listeners" ]; then block 'Selected port is occupied; choose another SONAR_PORT.'; else ok 'No listener detected on the selected port.'; fi
else
    unknown 'Port availability: no lsof or ss; verify at service startup.'
fi
recommend 'Keep 30 GB available disk and at least 10% free in the engine; inspect its VM/disk allocation separately.'
printf '[UNVERIFIED] Engine disk capacity/free space: engine usage alone does not establish VM free capacity.\n'
printf 'Package preflight and source validation still run before SonarQube startup. No configuration was changed.\n'
if [ "$BLOCKERS" -gt 0 ]; then exit 1; fi
if [ "$UNKNOWN" -gt 0 ]; then exit 3; fi
exit 0
