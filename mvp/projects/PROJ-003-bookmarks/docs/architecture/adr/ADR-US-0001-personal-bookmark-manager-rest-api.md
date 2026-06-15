# ADR-US-0001: Personal bookmark manager REST API

- Date: 2026-06-02
- Story: US-0001
- Status: proposed (pending architecture sign-off)

## Decision
Use a simple layered FastAPI app: routes validate/serialize requests, services contain all bookmark logic, repositories handle SQLAlchemy persistence, and models define the SQLite schema. Store bookmarks in a `bookmarks` table with generated integer `id`, `url`, `title`, optional `description`, and `created_at`; model tags with a small normalized association table so tag filtering and list responses stay simple and reliable. Implement pagination in the list endpoint with explicit `page` and `page_size` query params, plus tag filtering and a separate search endpoint that matches title/description with a case-insensitive SQL LIKE. Add centralized exception handling so all errors return `{detail, code}` consistently. Keep startup self-contained with SQLite table creation for local development and a `/health` endpoint for smoke tests.

## Affected modules
- repo/app/main.py
- repo/app/api/routes/bookmarks.py
- repo/app/api/routes/health.py
- repo/app/services/bookmarks.py
- repo/app/repositories/bookmarks.py
- repo/app/models/bookmark.py
- repo/app/models/tag.py
- repo/app/db/session.py
- repo/app/schemas/bookmark.py
- repo/app/core/errors.py
- repo/tests/api/test_bookmarks.py
- repo/tests/integration/test_persistence.py

## Data / API impact
- DB impact: yes
- API impact: yes
- Migration needed: no

## Constraints for implementation
- Use FastAPI dependency injection for SQLAlchemy sessions.
- Keep business logic out of routes and in services.
- Do not introduce auth, external services, or non-spec technologies.
- Return JSON only, including errors, with the required error shape.
- Use isolated temporary SQLite databases in tests.

## Risks
- Tag storage can become awkward if implemented as plain JSON; normalized tag tables reduce filtering risk.
- Search behavior must be case-insensitive and consistently defined to avoid flaky tests.
- Pagination contract needs a fixed default/page size to keep responses predictable.

## Breaking changes
- none

## Sensitivity
- none
