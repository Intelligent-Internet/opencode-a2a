# AGENTS.md

The following rules apply to coding agent collaboration and delivery workflows in this repository.

## 1. Core Principles

- Move tasks forward under secure and traceable conditions, while avoiding unnecessary process blockers.
- Stay consistent with the existing repository structure, implementation style, and engineering conventions.

## 2. Git Workflow

- Do not commit or push directly to protected branches: `main` / `master` / `release/*`.
- Each development task should be implemented on an independent branch, preferably cut from the latest mainline.
- Prefer `git fetch` + `git merge --ff-only` to sync mainline and avoid implicit merges.
- It is allowed to push development branches to remote branches with the same name for collaboration and backup.
- Do not rewrite shared history: no `git push --force`, `git push --force-with-lease`, or arbitrary `rebase`.
- Commit only files related to the current task; do not clean up or roll back unrelated local changes.

## 3. Issue and PR Collaboration

- Before starting a development task, check whether a related open issue already exists (for example, `gh issue list --state open`).
- If no related issue exists, create a new issue for tracking. The issue should include background, reproduction steps, expected vs. actual behavior, acceptance criteria, and a `git rev-parse HEAD` snapshot.
- Only collaboration-process documentation changes (such as `AGENTS.md`) can be modified directly without creating an additional issue.
- Recommended issue title prefixes: `[feat]`, `[bug]`, `[docs]`, `[ops]`, `[chore]`.
- If a commit serves a specific issue, include the corresponding `#issue` in the commit message.
- PRs are recommended to be created as Draft by default, and should explicitly indicate linkage in the description (for example, `Closes #xx` / `Relates to #xx`).
- When key progress, solution changes, or new risks appear, sync updates to the corresponding issue/PR in time and avoid duplicate comments.

## 4. Tooling and Text Conventions

- Use `gh` CLI to read and write issues/PRs; do not edit through the web UI manually.
- Use Simplified Chinese for issues, PRs, and comments; technical terms may remain in English.
- For multi-line bodies, write to a temporary file first and pass it with `--body-file`; do not concatenate `\\n` in `--body`.
- Use `#123` for same-repo references (auto-linking); use full URLs for cross-repo references.

## 5. Regression and Validation

- Choose regression strategy based on change type. Default baseline:
  - `uv run pre-commit run --all-files`
  - `uv run pytest`
- If `pre-commit` auto-fixes files (such as `ruff --fix`), review the changes before committing.
- For shell/deployment script changes, in addition to baseline checks, run at least `bash -n` for syntax validation on modified scripts.
- For documentation-only changes, tests may be skipped, but commands and path examples must be self-checked for usability.
- `uv sync --all-extras` is required only for first-time setup or dependency changes; it is not mandatory for every change.
- If any validation cannot be completed due to environment limits, explicitly state the skipped item and reason in the report.

## 6. Security and Configuration

- Never commit keys, tokens, credentials, or other sensitive information (including `.env` content).
- Logs and debug output must not leak access tokens or private data.
- Changes related to deployment, authentication, or secret injection must include synchronized documentation updates and minimal acceptance steps.
