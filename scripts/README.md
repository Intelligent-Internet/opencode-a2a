# scripts

Executable scripts live in this directory. This file is the entry index for the
remaining repository-maintenance helpers.

## Product Contract vs Script Docs

- Product/API behavior (transport, protocol contracts, extension semantics):
  - [`../docs/guide.md`](../docs/guide.md)
- Security boundary and disclosure guidance:
  - [`../SECURITY.md`](../SECURITY.md)

## Other Scripts

- [`doctor.sh`](./doctor.sh): local development regression entrypoint (`sync`/`pip check` + lint + tests)
- [`dependency_health.sh`](./dependency_health.sh): dependency review entrypoint (`sync`/`pip check` + outdated + audit)
- [`lint.sh`](./lint.sh): lint helper
- [`smoke_test_built_cli.sh`](./smoke_test_built_cli.sh): wheel-install smoke test for the released CLI runtime

## Notes

- `doctor.sh` and `dependency_health.sh` intentionally remain separate entrypoints and share common prerequisites through [`health_common.sh`](./health_common.sh).
