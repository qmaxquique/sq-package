# Claude Code installation and execution scope

The maintained source is `skills/claude/sq`. Claude Code discovers `/sq` from a supported skills directory, not from this source path automatically. Copy the complete folder: `SKILL.md`, `scripts/`, and `references/`. The port has no dependency on the Codex or Copilot skill directories. See the [Claude Code skills reference](https://code.claude.com/docs/en/skills).

## Install and invoke

For use across local projects, copy the folder to `~/.claude/skills/sq`. For one repository, copy it to `TARGET/.claude/skills/sq`. Preserve an existing `sq` entry, including a dangling symlink; inspect it before choosing another destination. The package README has a guarded copy command. A checked-in project copy is a separate repository change. Claude Code also accepts a symlinked skill folder, but this source is independently copyable.

Invoke `/sq analyze this checkout`, `/sq export the latest report for NAME`, or `/sq set up local SonarQube`; Claude can also select the skill automatically for a relevant request. `name: sq` determines `/sq` and the frontmatter description guides automatic selection. No `allowed-tools`, hooks, or permission bypass settings are bundled. Words supplied after `/sq` express the request; never paste them into a shell command without resolving and quoting the intended paths.

If `/sq` is missing, check the exact `SKILL.md` name, the installed directory, and `/skills`. Inspect other personal, project, enterprise, or plugin skills named `sq` before changing one: precedence can select a different skill. If the top-level skills directory was created after the session started, use `/reload-skills` if that command exists, otherwise restart Claude Code. Check that skill loading is not disabled by the session or managed policy. Do not update Claude Code or overwrite another skill merely to resolve discovery.

## Locate the package and allow access

An installed copy outside the package cannot identify an arbitrary existing package checkout by itself. Follow the entrypoint's discovery order. For a checkout separate from the source project, start Claude Code from the target checkout with an explicit package root and access to that directory:

```bash
SQ_PACKAGE_ROOT="/absolute/path/to/sq-package" \
  claude --add-dir "/absolute/path/to/sq-package"
```

`--add-dir` grants access to the package; the current working directory remains the scan target. In an existing session, `/add-dir PATH` can provide access. Add the source directory separately only when it is outside the working directory. Keep `SKILL_ROOT`, `PACKAGE_ROOT`, and `SOURCE_ROOT` distinct. The resolved package's `config/sq.local.properties` and engine histories are shared with the other `sq` ports when they use that same package installation.

Additional directory access does not automatically load that directory's `CLAUDE.md`, `.claude/rules/`, or other configuration. Inspect the *target checkout's* applicable `CLAUDE.md`, `CLAUDE.local.md`, and path-specific rules. Check and honor applicable `AGENTS.md` instructions explicitly when uncertain whether the installed Claude Code version loaded them. Instructions in the separate package do not replace those of the target. See [Claude Code directory permissions](https://code.claude.com/docs/en/permissions) and [instruction loading](https://code.claude.com/docs/en/memory).

For a newly cloned managed package, allow access to `~/.local/share/sq-package` when needed; then follow the entrypoint's install/version checks. A report-only request can use existing package state without the source checkout. Do not assume that granting file access also grants shell command approval.

## Sandbox and engine access

Claude Code's permission system may prompt for Bash, file writes, and access to directories outside the workspace. Use the normal approval flow for each needed action. This skill does not pre-approve them. If `doctor.sh` or `./sonar` fails, distinguish an actual engine/Compose/kernel failure from sandbox denial before changing the engine. Docker commands can fail inside Claude's sandbox; rootless Podman on Linux can also fail because user namespaces are unavailable. Where Claude Code offers an unsandboxed retry for the exact command, use that permission flow. If policy or the user denies it, report the command and required local action. Avoid global sandbox changes, `--dangerously-skip-permissions`, broad command exclusions, or weakening the package's kernel checks. See [sandbox troubleshooting](https://code.claude.com/docs/en/sandboxing).

## Remote and cloud execution

Paths, engine sockets, kernel checks, `localhost`, dashboard URLs, and report files belong to the host where Claude Code's terminal runs. A remote, container, or cloud session does not inherit the workstation's uncommitted files, container engine, services, or package state. Personal skills in `~/.claude/skills` are local to that machine. Establish the actual execution host and requested checkout before setup or analysis.

The package supports a local Docker or Podman engine accessible on that host. Containerized sessions may present checkout paths that the engine host cannot bind-mount; verify access through the package's validation and preflight, not just a file listing. When the checkout, local engine, kernel access, or administrative action is unavailable, state the exact blocker and action needed to continue. Provide paths and links that reflect the execution host, and do not claim workstation analysis from another host.
