# scripts

Executable scripts live in this directory. This file is the entry index for the remaining repository-maintenance helpers.

## Product Contract vs Script Docs

- Product/API behavior (transport, protocol contracts, extension semantics):
  - [`../docs/guide.md`](../docs/guide.md)
- Security boundary and disclosure guidance:
  - [`../SECURITY.md`](../SECURITY.md)

## Other Scripts

- [`doctor.sh`](./doctor.sh): primary local development regression entrypoint with explicit fix/verify/package phases (uv sync + dependency compatibility + lint + mypy + tests + coverage + hash-locked PEP 517 build + built-wheel smoke test)
- [`conformance.sh`](./conformance.sh): repository-owned A2A 1.0 black-box compatibility probes; can launch a dummy-backed local SUT and preserves machine-readable artifacts under `run/conformance/`
- [`live_opencode_smoke.sh`](./live_opencode_smoke.sh): local/manual live integration smoke against a real OpenCode runtime; boots an isolated password-hardened `opencode serve` plus the opencode-a2a runtime in throwaway directories and verifies upstream auth, JSON-RPC extension reads, and a streaming prompt round-trip
- [`dependency_health.sh`](./dependency_health.sh): development and build dependency review entrypoint (`sync`/`pip check` + outdated + strict hash-locked audits), while blocking CI/publish audits focus on runtime dependencies
- [`check_coverage.py`](./check_coverage.py): enforces the overall coverage floor and per-file minimums for critical modules
- [`find_thin_wrappers.py`](./find_thin_wrappers.py): static analysis helper that reports local functions with low caller counts and flags likely thin forwarding wrappers for manual abstraction review
- [`lint.sh`](./lint.sh): lint entrypoint used by CI; runs the repository `pre-commit` pipeline and **rejects hook-generated rewrites** instead of retrying them, so an unformatted or un-fixed commit cannot pass the gate
- [`validate_release_tag.sh`](./validate_release_tag.sh): verifies that a release input is an existing `v*` tag pointing at the checked-out commit before any audit or publish step runs
- [`smoke_test_built_cli.sh`](./smoke_test_built_cli.sh): built-artifact smoke test for the released CLI runtime; defaults to the only local wheel, supports explicit wheel/sdist paths, and rejects ambiguous local artifact selection

## Notes

- `doctor.sh` and `dependency_health.sh` intentionally remain separate entrypoints and share common prerequisites through [`health_common.sh`](./health_common.sh).
- `doctor.sh` covers the default local validation baseline, while `dependency_health.sh` remains focused on standalone dependency review and audit flow.
- `doctor.sh` stops early when `pre-commit` rewrites files so you can review the changes and rerun `doctor.sh` from the updated worktree.
- `lint.sh` shares the same working-tree fingerprint check through [`health_common.sh`](./health_common.sh): auto-fixing hooks (`trailing-whitespace`, `end-of-file-fixer`, `ruff --fix`, `ruff format`) make the gate fail instead of being masked by a retry.
- [`.github/dependabot.yml`](../.github/dependabot.yml) separates `a2a-sdk`, runtime framework, routine tooling/transitive, GitHub Actions, and pre-commit updates; all version updates use a cooldown.
- Dependency-manifest changes run development and build audits through the `Review Dependency Health` workflow and retain machine-readable reports.
- [`build-requirements/requirements.txt`](../build-requirements/requirements.txt) pins and hashes the isolated PEP 517 build closure. Dependabot maintains it through the separate `pip` ecosystem entry; update it together with `[build-system]` in `pyproject.toml`.
- `dependency_health.sh` remains the explicit local review/audit entrypoint.
- Repository-owned compatibility probes remain separate from the default regression path. See [`../docs/conformance.md`](../docs/conformance.md).
