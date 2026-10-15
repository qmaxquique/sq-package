# Copilot installation and execution scope

The versioned source is `skills/copilot/sq`. Copilot discovers a skill in a supported skill directory, not at this source path automatically. Keep the whole folder: `SKILL.md`, `scripts/`, and `references/`. A copied installation must work without the Codex skill or any `agents/openai.yaml` file.

## Install and invoke

For personal use across local projects, copy the folder to `~/.copilot/skills/sq`. For one repository, copy it to `TARGET/.github/skills/sq`; committing that copy is a separate repository change. Preserve an existing unrelated `sq` entry, including dangling symlinks. The package README includes a guarded personal installation command. Do not install both variants under the same name in one Copilot skills directory.

In Copilot CLI or VS Code agent mode, invoke `/sq analyze this checkout`, `/sq export the latest report for NAME`, or `/sq set up local SonarQube`. Copilot can also select it from a relevant natural-language request. The standard `name` and `description` frontmatter supplies discovery; default model invocation and user invocation remain enabled. The skill does not pre-approve shell tools.

For discovery problems, inspect the client's skills menu (`/skills` in an interactive session), verify the installation path and exact `SKILL.md` capitalization, and check whether another skill named `sq` takes precedence or the skill is disabled. Restart/reload the session if needed. Copilot versions differ in their non-interactive skill-management commands: use local help before suggesting one. Do not update Copilot implicitly.

## Locate the package and allow access

A copied skill outside the package does not reveal where an existing package was cloned. Follow the entrypoint's discovery order. To explicitly reuse an installation from another checkout, start Copilot CLI there with, for example:

```bash
SQ_PACKAGE_ROOT="/absolute/path/to/sq-package" \
  copilot --add-dir "/absolute/path/to/sq-package"
```

The current directory is the source project. `--add-dir` grants access to the separate package; it does not change the scan target. If the source or skill resources are also outside permitted directories, add only those required directories. In an existing CLI session use `/add-dir PATH` when access is needed. In VS Code, use the client's file/terminal approval flow for the package and source. Keep `SKILL_ROOT` distinct from both. Respect normal tool/path approvals instead of enabling global permission bypasses.

When the managed location is needed, allow access to `~/.local/share/sq-package` as appropriate, then clone there using the entrypoint's procedure. Shared engine preferences live in the resolved package's `config/sq.local.properties`, not in Copilot settings; Codex and Copilot using that package share its configuration and selected-engine history.

## Remote and cloud sessions

Paths, engine sockets, kernel checks, and `localhost` refer to the host where Copilot's terminal tools execute. A cloud agent, Codespace, SSH workspace, or dev container does not inherit the workstation's package, uncommitted files, services, or history. Identify that host and the actual target before preparing anything.

Use this workflow only where the package, requested checkout, and a supported local Docker/Podman engine are accessible on that execution host. A containerized session may expose paths that do not match its engine host's bind-mount paths; verify actual source access and never call it ready from file visibility alone. Preserve an existing engine/context and the package's locality restrictions.

If the requested workstation checkout is unavailable, or engine/kernel/administrator access is restricted, explain the specific missing capability and the exact local step needed to continue. Do not substitute a different checkout or claim workstation analysis. Dashboard/report links are usable on the execution host; disclose this boundary and provide actual paths. Do not publish findings or forward ports as part of an ordinary scan.

## Format references

- [GitHub: adding agent skills](https://docs.github.com/en/copilot/how-tos/copilot-on-github/customize-copilot/customize-cloud-agent/add-skills)
- [VS Code: agent skills and slash commands](https://code.visualstudio.com/docs/agent-customization/agent-skills)
- [Copilot CLI: commands, directory access, and skill management](https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-command-reference)
