---
name: sq
description: Set up and operate sq-package, the local SonarQube toolkit for PHP/Python. Use for sq or local SonarQube scans, HTML/CSV reports, quality gates, prerequisites, and recovery. Generic code review requests alone do not call for running SonarQube.
---

# sq

Use the package's `sonar` wrapper for normal operations. Default to analyzing the requested working tree, exporting HTML/CSV, and explaining actionable findings. Analysis alone does not authorize application fixes or executing its tests.

Use `/sq` in Claude Code, or let Claude select this skill for a relevant request. Treat text following `/sq` as the task, never as shell code. Use a session with file and terminal access on the intended execution host. Read [Claude Code installation and access](references/claude.md) for discovery, directory permissions, sandbox errors, or remote sessions. A generic code review alone does not call for starting SonarQube.

Claude Code substitutes `${CLAUDE_SKILL_DIR}` in this skill's content. Resolve the physical skill directory before discovering the package, including when the skill was loaded through a symlink:

```bash
SKILL_ROOT=$(cd -- "${CLAUDE_SKILL_DIR}" && pwd -P)
```

## Resolve the installation and engine

1. Locate the package in this order: explicit `SQ_PACKAGE_ROOT`; a containing package checkout found by walking up from the current directory, then from this skill's **resolved physical directory**; `~/.local/share/sq-package`. A package has executable `sonar`, `compose.yaml`, `compose.external.yaml`, and `scripts/sonar.py`.
2. An invalid explicit root is an error, not permission to use another installation. If no installation exists, clone `https://github.com/qmaxquique/sq-package.git` into the managed location. Never pull/reset an existing checkout implicitly. Check `sonar help` advertises `--path`; explain an incompatible package version rather than pretending it supports this skill.
3. Read `config/sq.local.properties` as data, never source/evaluate it. Choose engine by explicit request, then `SONAR_ENGINE`, then saved `engine`. If none is selected, ask Docker or Podman once and save the choice. Never switch engines because one failed: their histories and credentials are separate.
4. Resolve port from explicit request, `SONAR_PORT`, saved `<engine>.port`, then `9000`. Persist a chosen default or alternate port. Temporary overrides do not change the saved default unless requested. The settings file contains only `engine`, `docker.port`, and `podman.port`; no credentials. Preserve other engine settings when updating it.
5. Resolve the original target's applicable instructions, including `CLAUDE.md`, `CLAUDE.local.md`, matching `.claude/rules/` files, and applicable `AGENTS.md`. Read relevant files explicitly when their automatic loading is uncertain. Retain separate absolute paths for `PACKAGE_ROOT`, `SOURCE_ROOT`, and `SKILL_ROOT`; run wrapper commands from the package directory.

## Diagnose and prepare

Read [setup](references/setup.md) on first setup, missing requirements, or kernel/VM failures. Requests to set up or run sq include preparing its prerequisites within existing permissions. Reuse installations and machines; do not upgrade unrelated software. Run the bundled [diagnostic helper](scripts/doctor.sh):

```bash
bash "$SKILL_ROOT/scripts/doctor.sh" --package "$PACKAGE_ROOT" \
  --engine "$ENGINE" --source "$SOURCE_ROOT" --port "$PORT"
```

The helper is read-only: exit `0` means checked prerequisites passed, `1` means blockers, `2` invalid usage, `3` required checks remain unverified. Treat recommendations separately from mandatory requirements. Unverified VM kernel/sharing checks call for the setup procedure and real package validation, not a claim of readiness. Fix diagnosed blockers and retry the relevant check. If access or interactive installation blocks progress, give the exact needed user action and resume afterward; do not retry the same failure indefinitely.

Omit `--source` for report-only or service requests that do not need local code.

## Analyze a checkout

Read [configuration and commands](references/usage.md) when selecting scope, configuring imports, operating services, or interpreting results.

1. For an explicitly selected repository already under `PACKAGE_ROOT/projects/`, keep its exact folder name and existing history. Do not accidentally analyze sq-package itself just because it is the command directory.
2. Otherwise canonicalize the requested checkout (Git root, or requested directory for non-Git code), then obtain its stable name with the bundled [project-name helper](scripts/project-name.sh):
   ```bash
   NAME=$(bash "$SKILL_ROOT/scripts/project-name.sh" "$SOURCE_ROOT")
   ```
   Separate checkout paths get separate histories. Branch switches in one directory keep that directory's history. Do not replace a source binding to force reuse.
3. Preserve existing `config/projects/$NAME.properties`. Infer sources/tests only from actual structure; start with package defaults where sufficient. Retain the package's default exclusions when extending them. For WordPress, ask which themes/plugins are owned when ownership is unclear.
4. Inspect Git metadata with read-only Git commands (`GIT_OPTIONAL_LOCKS=0`). If `.git` is a file, or the resolved Git/common directory is outside the source, configure both `sonar.scm.disabled=true` and `sonar.scm.exclusions.disabled=true` for this checkout. This supports linked worktrees without mounting or rewriting Git metadata. Disclose that blame and SCM-derived new-code detection are unavailable and scope relies on configured exclusions.
5. Import only verified existing coverage/test reports with explicit repository-relative paths. Do not install application dependencies, generate coverage, execute tests, or claim fresh coverage unless requested. Identify static-only analysis and report freshness limitations.
6. Execute from `PACKAGE_ROOT`, quoting all paths:
   ```bash
   SONAR_PORT="$PORT" ./sonar --engine "$ENGINE" scan "$NAME" --path "$SOURCE_ROOT"
   ```
   Omit `--path` for package-managed repositories. Add `--check-gate` only when gate enforcement is requested. `0` means execution succeeded, `1` means configuration/scanner/server failure, `2` means a completed analysis failed gate enforcement. A failed gate still has published results.
7. After a completed scan (including exit `2`), export:
   ```bash
   SONAR_PORT="$PORT" ./sonar --engine "$ENGINE" report "$NAME"
   ```
   Use the printed export path; do not guess the newest directory. Reports describe the latest completed analysis and current review states, not a transactional snapshot. If another scan could have intervened, compare task/analysis provenance and disclose mismatches instead of attributing another analysis to this run.

## Other requests and recovery

- Report-only: export existing results without rescanning or changing configuration. If stopped, use `up` to restore the same stack. No local source is required.
- Diagnostics/status/logs/up/down: perform the requested operation without automatically scanning or exporting.
- Fix findings: confirm them against original source, edit that checkout, run relevant existing tests, then rescan/export. Do not treat hotspots as confirmed vulnerabilities or weaken gates/exclusions to manufacture a pass.
- Use [troubleshooting](references/troubleshooting.md) for locks, timeouts, credentials, tokens, imports, and export failures. Never reset history as ordinary repair. Reset requires an explicit deletion request. Display the password only for an explicit credentials request.

## Return useful evidence

State original checkout and dirty/revision context, project name, engine/port, analyzed scope, execution outcome, and quality gate. Link the dashboard and all three report files with absolute local paths. Summarize important findings using original source file/line links, separate hotspots requiring review, and identify coverage/SCM limitations. Do not claim tests or live verification that did not run. If blocked, report the observed blocker and exact next action; never include saved secrets.
