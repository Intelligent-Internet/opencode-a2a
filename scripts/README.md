# scripts

Executable scripts live in this directory. This file is the entry index for the
remaining repository-maintenance helpers.

## Product Contract vs Script Docs

- Product/API behavior (transport, protocol contracts, extension semantics):
  - [`../docs/guide.md`](../docs/guide.md)
- Security boundary and disclosure guidance:
  - [`../SECURITY.md`](../SECURITY.md)

## Other Scripts

- [`doctor.sh`](./doctor.sh): primary local development regression entrypoint (uv sync + lint + tests + coverage)
- [`dependency_health.sh`](./dependency_health.sh): development dependency review entrypoint (`sync`/`pip check` + outdated + dev audit), while blocking CI/publish audits focus on runtime dependencies
- [`check_coverage.py`](./check_coverage.py): enforces the overall coverage floor and per-file minimums for critical modules
- [`lint.sh`](./lint.sh): lint helper
- [`smoke_test_built_cli.sh`](./smoke_test_built_cli.sh): built-artifact smoke test for the released CLI runtime; defaults to the only local wheel, supports explicit wheel/sdist paths, and rejects ambiguous local artifact selection

## Notes

- `doctor.sh` and `dependency_health.sh` intentionally remain separate entrypoints and share common prerequisites through [`health_common.sh`](./health_common.sh).
