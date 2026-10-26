# Configuration, commands, and results

## Source selection

- Normal default invocation: requested Git checkout root, including current edits. For non-Git code, use the requested directory. Honor an explicitly narrowed target instead of silently broadening it.
- Existing named package project: `scan NAME`; external checkout: `scan NAME --path "$SOURCE_ROOT"`. `--all` is only for an explicit request to analyze all package-managed repositories. It does not enumerate registered external sources.
- Generate external names with the bundled `project-name.sh`: canonical host path SHA-256, sanitized basename capped at 128 characters, 12 digest characters. Preserve the returned name exactly. Moving the checkout produces another project; do not rename directories to manipulate history.
- Never stage copies or symlink a repository into `projects/`. Native external binding mounts only the selected source at `/external-project`; scanner output stays in package state.

## Project configuration

Effective configuration is `config/defaults.properties` plus `config/projects/NAME.properties` in the package. Clone-local `sonar-project.properties` is not automatically loaded. Read defaults before editing; overrides **replace** matching values. Keep dependency/cache exclusions when appending custom patterns.

Use explicit existing repository-relative paths for `sonar.sources`, `sonar.tests`, and report files. Directory roots are comma-separated paths, not globs. Test roots are automatically excluded from production sources. Colocated tests require, for example:

```properties
sonar.tests=.
sonar.test.inclusions=**/test_*.py,**/*_test.py
```

Keep project identity, connection, token, working-directory, branch/PR, scanner-connection, and sensitive logging settings under package control. Ordinary scans keep the default quality profiles/gates. Community Build uses one timeline per project, not branch/PR analysis.

**Python:** optional `sonar.python.version` should match supported application versions; infer it from actual project metadata, not the tools-container Python version. PHP suffixes and `.py` are selected by default. A custom suffix also needs a matching inclusion pattern. Older PHP-only inclusions must explicitly add Python to analyze both.

**WordPress:** select explicit owned theme/plugin source paths. Neither the template's placeholder names nor every installed plugin is an ownership decision. Exclude core/third-party code through source selection. A standalone owned plugin/theme can use `sonar.sources=.`.

**Linked worktrees/external Git metadata:** detect without changing Git state:

```bash
GIT_OPTIONAL_LOCKS=0 git -C "$SOURCE_ROOT" rev-parse --absolute-git-dir
GIT_OPTIONAL_LOCKS=0 git -C "$SOURCE_ROOT" rev-parse --git-common-dir
```

Resolve relative common-directory output against the source before comparing containment. A `.git` file also covers linked worktrees/submodule checkouts. For this v1 mode configure both:

```properties
sonar.scm.disabled=true
sonar.scm.exclusions.disabled=true
```

Preserve the checkout; do not mount external Git metadata or rewrite `.git`. Without SCM exclusions, configured source scope/default exclusions must exclude generated/dependency code. Explain that blame and SCM-derived new-code detection are unavailable. See [SonarQube SCM integration](https://docs.sonarsource.com/sonarqube-community-build/analyzing-source-code/scm-integration).

## Coverage and tests

Import existing files only after verifying their format, file paths, and relevant source/test roots. Do not enable a property pointing to a missing file or a wildcard. Do not infer executed coverage from static findings, a zero metric, or the presence of a test directory.

| Language | Property | Format |
| --- | --- | --- |
| PHP coverage | `sonar.php.coverage.reportPaths` | Clover XML; comma-separated explicit files |
| PHP tests | `sonar.php.tests.reportPath` | PHPUnit JUnit XML; one file |
| Python coverage | `sonar.python.coverage.reportPaths` | Coverage.py/pytest-cov Cobertura XML; comma-separated explicit files |
| Python tests | `sonar.python.xunit.reportPath` | xUnit XML; one file |

Report paths inside XML must be repository-relative or match the scanner mount (`/external-project/` for external sources, `/projects/NAME/` otherwise). Package validation checks file presence/containment; the scanner validates report content. Inspect freshness when ascertainable; do not claim reports reflect the current working tree without evidence.

When coverage generation is explicitly requested, inspect existing project/CI commands and use its development environment. Avoid modifying source or installing dependencies for an ordinary analysis. For an existing Python pytest/Coverage.py setup, a portable example is `[run] relative_files=True` and the application's actual source root in Coverage.py config, then:

```bash
coverage run -m pytest -o junit_family=xunit1 --junitxml=coverage/junit.xml
coverage xml -o coverage/coverage.xml
```

Ensure the output directory exists; don't overwrite an existing test configuration with this generic example. For PHP, use the repository's PHPUnit version/configuration to generate Clover/JUnit. Static-only scans remain useful; gate coverage expectations can fail without imported coverage.

## Operations

All commands run from the package directory with the same explicit engine and `SONAR_PORT`:

| Request | Wrapper command |
| --- | --- |
| Start/setup | `up` |
| Analyze | `scan NAME [--path PATH] [--check-gate]` |
| Export existing analysis | `report NAME` |
| Inspect | `status`, `logs [sonarqube\|postgres]` |
| Show requested login | `credentials` |
| Stop, preserve history | `down` |
| Explicitly delete selected engine data | `reset --yes` |

Exit `1` is an execution/configuration error. Exit `2` occurs only with `--check-gate` and any non-OK gate, including unconfigured/warning states. Without enforcement, a failed gate is informational; still export the completed results. For `--all`, configuration is validated up front; runtime scan failures continue and take priority over gate failures.

Timeout environment variables: startup/analysis `300`, scanner `1800`, report `600` seconds by default. Increase the relevant one after diagnosing a slow operation. A processing timeout may still publish later; inspect before submitting another scan.

## Reports and interpretation

Use the command's printed path. Docker exports under `.state/reports/NAME/EXPORT_ID/`, Podman under `.state/podman/reports/NAME/EXPORT_ID/`. Keep `index.html`, `issues.csv`, and `security-hotspots.csv` together; HTML is self-contained and links to its sibling CSVs. The HTML shows the first 100 issues by severity; CSVs include all returned findings, including resolved issues. Separate unresolved actionable findings from resolved entries when summarizing. Hotspots require review and are not automatically confirmed vulnerabilities.

Exports use the latest completed analysis and current review states. They reject incomplete/changing counts and an analysis completing during collection; equal-count review changes can still escape detection. Check analysis/task provenance when another process could have scanned between commands. Do not assert the report belongs to a particular submitted task without matching evidence.

Map CSV file paths to original source paths, not container paths, for clickable file/line links. Show gate, coverage availability, meaningful counts and top findings without flooding chat. Return all three absolute file links and the dashboard link. Findings can contain sensitive paths/messages: do not publish or send them elsewhere without a request. No credentials belong in a summary.
