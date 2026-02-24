# Uninstall Guide (`uninstall.sh`)

This document describes `scripts/uninstall.sh`, which removes one deployed project instance.

## Safety Model

- Preview-first by default.
- Destructive actions run only when `confirm=UNINSTALL` is provided.
- Shared systemd template units are never removed.

## Usage

Preview:

```bash
./scripts/uninstall.sh project=<project>
```

Apply:

```bash
./scripts/uninstall.sh project=<project> confirm=UNINSTALL
```

Optional:

- `data_root=/data/opencode-a2a`

## Guardrails

- validates `project` and `data_root` format
- uses canonical path checks before delete actions
- in apply mode, requires `sudo` and strict project-name constraints
- does best-effort handling for non-critical cleanup failures

## Related Docs

- deployment flow: [`deploy_readme.md`](./deploy_readme.md)
- local run flow: [`start_services_readme.md`](./start_services_readme.md)
