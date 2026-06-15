# ADR-US-0015: Notes REST API with SQLite persistence

- Date: 2026-06-03
- Story: US-0015
- Status: proposed (pending architecture sign-off)

## Decision
Use a simple layered FastAPI design: routes handle HTTP only, services own validation/business rules, repositories own SQLAlchemy queries, and models/schemas separate persistence from API contracts. Persist notes in SQLite with a normalized tags table so exact tag filtering and pagination work reliably without SQLite-specific JSON tricks. Expose a single health endpoint and keep all errors on the required {detail, code} shape via shared exception handling.

## Affected modules
- app/main.py
- app/db.py
- app/models.py
- app/schemas.py
- app/routes/notes.py
- app/services/notes.py
- app/repositories/notes.py
- app/core/errors.py
- tests/test_notes_api.py

## Data / API impact
- DB impact: yes
- API impact: yes
- Migration needed: no

## Constraints for implementation
- Keep business logic out of routes; routes only validate input, call services, and translate exceptions.
- Use dependency-injected SQLAlchemy sessions everywhere; no global session usage.
- Use transaction-safe create/update/delete flows so note and tag rows stay consistent.
- Paginate every collection-returning endpoint, including search.
- Return consistent error bodies with both detail and code for 404 and validation/business errors.
- Keep the API RESTful and JSON-only; no auth, no GraphQL, no WebSockets.

## Risks
- Tag filtering with normalized rows requires careful count/distinct handling so pagination metadata stays correct.
- Search semantics on SQLite can vary if case sensitivity or collation is not handled consistently.
- Without migrations, future schema evolution will need a clear follow-up path if the app grows.

## Breaking changes
- none

## Sensitivity
- none
