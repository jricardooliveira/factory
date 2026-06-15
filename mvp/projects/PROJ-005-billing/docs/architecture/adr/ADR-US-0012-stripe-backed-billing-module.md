# ADR-US-0012: Stripe-backed billing module

- Date: 2026-06-03
- Story: US-0012
- Status: proposed (pending architecture sign-off)

## Decision
Implement as a small billing subdomain with route -> service -> repository layering. Keep Stripe calls behind a thin gateway adapter so tests can mock it and the core service remains deterministic. Use a single billing profile per user to store billing address plus safe card metadata, and append-only charge records for audit/history. Because the story requires authenticated user scoping but the project scaffold forbids introducing auth for now, wire the module to an injectable current-user dependency that can be backed by the app's existing placeholder user context during initial development.

## Affected modules
- repo/app/main.py
- repo/app/api/routes/billing.py
- repo/app/services/billing_service.py
- repo/app/repositories/billing_repository.py
- repo/app/models/billing_profile.py
- repo/app/models/billing_charge.py
- repo/app/schemas/billing.py
- repo/app/dependencies/db.py
- repo/app/dependencies/user.py
- repo/app/integrations/stripe_client.py
- repo/migrations/versions/*_billing.py
- repo/tests/test_billing.py

## Data / API impact
- DB impact: yes
- API impact: yes
- Migration needed: yes

## Constraints for implementation
- Use SQLAlchemy 2.x models and sessions via dependency injection.
- Keep all business rules in the service layer; routes should only parse/validate/return.
- Persist charge attempts even on Stripe failure so history is auditable.
- Use integer cents for amounts; do not use floats for money.
- Do not store raw card data anywhere in the app or logs.
- Paginate the history endpoint.
- Use isolated temporary SQLite databases in tests and mock the Stripe gateway.

## Risks
- The story requires authenticated user scoping, but the base project spec says no auth for the initial scaffold; a placeholder/current-user dependency is needed until real auth exists.
- Charging later requires storing a safe Stripe reference (customer/payment_method id), which is not raw card data but is still an extra persisted identifier beyond last4/brand.
- Stripe API failures can create consistency gaps if a charge succeeds externally but the DB write fails; a pending-then-update persistence flow reduces this risk.

## Breaking changes
- none

## Sensitivity
- financial
- security
- pii
- compliance
