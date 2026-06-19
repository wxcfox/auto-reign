# Repository Guidelines

## Project Structure & Module Organization

This repository currently has no source tree, tests, or build configuration checked in. As code is added, keep the layout predictable:

- `src/` for application or library source code.
- `tests/` for automated tests that mirror the source structure.
- `assets/` for static files such as images, fixtures, or sample data.
- `docs/` for design notes, setup guides, and user-facing documentation.

Avoid placing implementation files at the repository root except for project metadata and configuration files.

## Build, Test, and Development Commands

No build or test commands are currently defined. When a toolchain is added, document the canonical commands here and prefer repository scripts over ad hoc commands. Examples:

- `npm test` or `pytest` to run the full test suite.
- `npm run build` or `make build` to produce distributable artifacts.
- `npm run dev` or equivalent to start a local development server.

Keep command names stable so contributors and automation can rely on them.

## Coding Style & Naming Conventions

Use the style conventions of the language or framework introduced in `src/`. Add formatter and linter configuration with the first substantial code contribution. Prefer clear, descriptive names: modules and files should use lowercase names with separators appropriate to the language, such as `task-runner.ts` or `task_runner.py`. Keep functions focused, avoid hidden side effects, and place shared utilities in a clearly named module rather than duplicating logic.

## Testing Guidelines

Add tests with each behavioral change. Test files should live under `tests/` and use names that identify the unit or workflow being verified, such as `test_scheduler.py` or `task-runner.test.ts`. Cover expected behavior, edge cases, and failure paths. If coverage tooling is introduced, document the required threshold and command in this file.

## Commit & Pull Request Guidelines

This directory does not currently expose Git history, so no repository-specific commit convention can be inferred. Use short, imperative commit messages such as `Add scheduler tests` or `Document setup steps`. Pull requests should include a concise summary, test evidence, linked issues when applicable, and screenshots for user-facing UI changes.

## Security & Configuration Tips

Do not commit secrets, local credentials, generated dependency folders, or machine-specific configuration. Provide sample environment files such as `.env.example` when configuration is required, and document every required variable.
