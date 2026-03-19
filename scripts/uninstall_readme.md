# Uninstall Guide (`opencode-a2a-server uninstall-instance`)

This document describes `opencode-a2a-server uninstall-instance`, which removes
one deployed project instance.

`scripts/uninstall.sh` remains available as a compatibility wrapper.

## Safety Model

- Preview-first by default.
- Destructive actions run only when `--confirm UNINSTALL` is provided.
- Shared systemd template units are never removed.

## Usage

Preview:

```bash
opencode-a2a-server uninstall-instance --project <project>
```

Apply:

```bash
opencode-a2a-server uninstall-instance --project <project> --confirm UNINSTALL
```

Optional:

- `--data-root /data/opencode-a2a`

## Guardrails

- validates `project` and `data_root` format
- uses canonical path checks before delete actions
- in apply mode, requires `sudo` and strict project-name constraints
- does best-effort handling for non-critical cleanup failures
- does not delete Linux service users or groups

## Related Docs

- formal deployment flow: [`deploy_release_readme.md`](./deploy_release_readme.md)
- source-based debug deployment: [`deploy_readme.md`](./deploy_readme.md)
