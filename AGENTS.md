# AGENTS.md — Home-view-graph

## Purpose

- Build a local-first, inspectable knowledge graph and search surface for a user-selected directory.
- Keep corpus selection, extraction, search, visualisation, updates, and integrations measurable and honest about partial results.

## Ownership

- `TODO.md` owns every active checklist, checkpoint, priority, and measured completion record.
- `DECISIONS.md` owns durable technical decisions and rejected alternatives.
- `README.md` owns the user-facing capabilities and commands that actually ship.
- `homegraph/` owns the Python package; `tests/` owns executable gates and mutation drivers.

## Local Contracts

- Preserve the local-first and offline-first default; network providers and external applications are opt-in.
- Preserve `dependencies = []` unless a measured decision in `DECISIONS.md` explicitly changes it.
- Never index secrets, cache trees, temporary files, generated exports, or package-owned databases.
- Search and integration results must label missing stores, incomplete vector coverage, and unavailable external systems.
- A checkpoint is complete only with a commit SHA or measured acceptance result recorded in `TODO.md`.

## Work Guidance

- Read `TODO.md`, the relevant `DECISIONS.md` sections, and the affected implementation before changing behavior.
- Write the acceptance gate and its expected answer before implementation.
- Add a production caller and mutation coverage for every new mechanism.
- Run new behavior on a synthetic corpus and a user-approved real corpus before calling it complete.
- Update `TODO.md` immediately after each checkpoint, not in a final batch.

## Verification

- `uvx --with-editable . pytest -q tests/`
- `uvx ruff check homegraph/ tests/`
- `uvx mypy homegraph/`
- `pyright`
- Run the checkpoint's mutation harness and record the named-gate verdict in `TODO.md`.
- Run the privacy gate from the main checkout where its private fixture exists.

## Child DOX Index

