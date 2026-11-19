# Contributing to repoctl

Describe the problem, the behavior that changes, and the checks you actually ran. Issues and pull requests may use English or Korean.

## Development

Work in the repoctl source checkout:

```sh
uv sync --locked --extra structural
uv run --locked --extra structural pytest -q
git diff --check
```

During development, run existing tests relevant to the change. A bug fix should include a case that fails before the fix and checks the observable result. Report unavailable optional analyzer coverage separately from failures.

The independent template owns its document examples. repoctl owns their parsing and CLI behavior. Conformance fixtures live in `tests/fixtures/template-contract-2.0.0/`; update them deliberately with a contract change.

## Releases

`pyproject.toml` owns the runtime version. The development builder at `.github/scripts/build_release_archive.py` generates the actual release archive, manifest, and checksums from the current source. Tests and the tag workflow use the same builder.

Published tags and assets describe their original source. A new release gets a new version. Adopters running from Git can update their checkout directly.

Use existing project conventions and include only documentation needed to understand the resulting behavior.
