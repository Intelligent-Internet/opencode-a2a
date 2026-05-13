# AGENTS.md

The following rules apply to coding agent collaboration in this repository. These complement the general [CONTRIBUTING.md](CONTRIBUTING.md) workflow.

## 1. Core Principles

- Move tasks forward under secure and traceable conditions.
- Adhere to the existing repository structure and engineering conventions.

## 2. Collaboration Workflow

- Follow the Git, Issue, and PR workflow defined in [CONTRIBUTING.md](CONTRIBUTING.md).
- Use `gh` CLI for all issue/PR operations (reading, writing, and comments). Do not edit through the web UI.
- Do not use the Codex GitHub connector for any repository operation. Use `gh` CLI exclusively for issue, PR, review, comment, and metadata workflows.
- If `gh` CLI cannot complete a required GitHub operation because of permissions, authentication, or environment limits, stop and ask the human to perform or unblock the operation. Do not fall back to the Codex GitHub connector.
- Create a new tracking issue for any development task that does not already have one.
- Link the issue explicitly in PR descriptions (e.g., `Closes #xx`).
- Keep status updates synchronized to the relevant issue/PR to avoid duplicate manual work.

## 3. Tooling and Text Conventions

- **Language**: Use Simplified Chinese for issues, PRs, and comments. Technical terms may remain in English.
- **PR Body**: For multi-line bodies, write to a temporary file first and pass it via `gh pr create --body-file`.
- **References**: Use `#123` for same-repo references; use full URLs for cross-repo links.

## 4. Regression and Validation

- Use the primary validation entrypoint for all changes:
  ```bash
  ./scripts/doctor.sh
  ```
- If `pre-commit` (via `doctor.sh`) auto-fixes files, review the changes before committing.
- For documentation-only changes, you may skip tests but must self-check all commands and paths.
- If environment limits prevent full validation, explicitly report what was skipped and why.

## 5. Security and Configuration

- **Secrets**: Never commit keys, tokens, or `.env` content.
- **Logs**: Ensure debug output does not leak access tokens.
- **Documentation**: Synchronize documentation updates for all deployment or auth-related changes.
