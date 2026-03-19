# scripts

Executable scripts live in this directory. This file is the entry index for script usage and script-specific documentation.

## Product Contract vs Script Docs

- Product/API behavior (transport, protocol contracts, extension semantics):
  - [`../docs/guide.md`](../docs/guide.md)
- Operator-facing deploy SOP:
  - [`../docs/agent_deploy_sop.md`](../docs/agent_deploy_sop.md)
- Security boundary and disclosure guidance:
  - [`../SECURITY.md`](../SECURITY.md)
- Script operational details (how to run and operate each script):
  - kept in this `scripts/` directory as `*_readme.md`

## Script Docs Index

- [`init_release_system_readme.md`](./init_release_system_readme.md): release-based host bootstrap for formal deployments (script: [`init_release_system.sh`](./init_release_system.sh))
- [`init_system_readme.md`](./init_system_readme.md): source-based host bootstrap for contributor/internal debugging (script: [`init_system.sh`](./init_system.sh))
- [`deploy_release_readme.md`](./deploy_release_readme.md): release-based multi-instance systemd deployment (script: [`deploy_release.sh`](./deploy_release.sh))
- [`deploy_readme.md`](./deploy_readme.md): source-based multi-instance systemd deployment for contributor/internal debugging (script: [`deploy.sh`](./deploy.sh))
- [`uninstall_readme.md`](./uninstall_readme.md): preview-first instance removal (script: [`uninstall.sh`](./uninstall.sh))

## Other Scripts

- [`doctor.sh`](./doctor.sh): local development regression entrypoint (`sync`/`pip check` + lint + tests)
- [`dependency_health.sh`](./dependency_health.sh): dependency review entrypoint (`sync`/`pip check` + outdated + audit)
- [`lint.sh`](./lint.sh): lint helper

## Notes

- `deploy/` contains helper scripts orchestrated by `deploy.sh` and `deploy_release.sh`.
- `doctor.sh` and `dependency_health.sh` intentionally remain separate entrypoints and share common prerequisites through [`health_common.sh`](./health_common.sh).
- Keep script behavior details in `scripts/*_readme.md` to avoid drift.
