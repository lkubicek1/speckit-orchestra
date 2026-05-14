# Agent Instructions

## Non-Negotiable Versioning Requirement

Any change that affects the installed package must update the package version before the work is considered complete.

This is required because `uv tool install`, `uv tool upgrade`, pip upgrades, and Git-based installs rely on package metadata. If code changes ship without a new version, users can reinstall and still appear to have the same build.

For every product change, update both version declarations in the same commit:

- `pyproject.toml` `[project].version`
- `src/speckit_orchestra/__init__.py` `__version__`

Product changes include CLI behavior, command flags, prompts, adapters, validation rules, migrations, generated artifact schemas, runtime behavior, dependency changes, packaging metadata, and user-facing documentation that describes installed behavior.

Use SemVer while the package is pre-1.0:

- Patch bump for fixes, small behavior changes, and documentation corrections tied to installed behavior.
- Minor bump for new commands, new options, new adapters, schema changes, or notable capabilities.
- Major bump only if the project intentionally leaves the current compatibility line.

Do not finish an implementation, create a release commit, or tell the user the work is complete until the version has been checked. If unsure whether a change needs a bump, bump the patch version.

Documentation-only agent/process guidance, tests that do not change package behavior, and repository maintenance may skip the version bump, but mention that decision in the handoff.

Before final handoff, run:

```bash
uv run pytest
```

At minimum, the test suite must keep `pyproject.toml` and `src/speckit_orchestra/__init__.py` versions in sync.
