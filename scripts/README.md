# scripts

Executable scripts live in this directory. This file is the entry index for the remaining repository-maintenance helpers.

## Product Contract vs Script Docs

- Product/API behavior (transport, protocol contracts, extension semantics): [`../docs/guide.md`](../docs/guide.md)
- Security boundary and disclosure guidance: [`../SECURITY.md`](../SECURITY.md)

## Other Scripts

- [`doctor.sh`](./doctor.sh): primary local development regression entrypoint (uv sync + dependency compatibility + lint + mypy + tests + coverage + built-wheel smoke test)
- [`conformance.sh`](./conformance.sh): local/manual external A2A conformance experiment entrypoint; caches the official TCK, can launch a dummy-backed local SUT, and preserves raw artifacts under `run/conformance/`
- [`dependency_health.sh`](./dependency_health.sh): development dependency review entrypoint (`sync`/`pip check` + outdated + dev audit), while blocking CI/publish audits focus on runtime dependencies
- [`check_coverage.py`](./check_coverage.py): enforces the overall coverage floor and per-file minimums for critical modules
- [`find_thin_wrappers.py`](./find_thin_wrappers.py): static analysis helper that reports local functions with low caller counts and flags likely thin forwarding wrappers for manual abstraction review
- [`lint.sh`](./lint.sh): lint helper
- [`smoke_test_built_cli.sh`](./smoke_test_built_cli.sh): built-artifact smoke test for the released CLI runtime; defaults to the only local wheel, supports explicit wheel/sdist paths, and rejects ambiguous local artifact selection

## Notes

- `doctor.sh` and `dependency_health.sh` intentionally remain separate entrypoints and share common prerequisites through [`health_common.sh`](./health_common.sh).
- `doctor.sh` covers the default local validation baseline, while `dependency_health.sh` remains focused on standalone dependency review and audit flow.
- [`.github/dependabot.yml`](../.github/dependabot.yml) prefers a single weekly grouped Dependabot PR for `uv`, while `dependency_health.sh` remains the explicit review/audit entrypoint.
- External conformance experiments remain intentionally separate from the default regression path. See [`../docs/conformance.md`](../docs/conformance.md).
