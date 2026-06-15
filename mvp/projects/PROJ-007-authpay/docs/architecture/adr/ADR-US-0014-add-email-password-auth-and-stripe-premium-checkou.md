# ADR-US-0014: Add email/password auth and Stripe premium checkout

- Date: 2026-06-03
- Story: US-0014
- Status: proposed (pending architecture sign-off)

## Decision
Add a minimal layered auth/payments subsystem. Use PBKDF2-HMAC-SHA256 with a per-user salt for password storage, and issue opaque bearer session tokens whose hashes are stored in SQLite. Protect /me and /checkout with a FastAPI dependency that resolves the token to an active session and user. Model account tier as free/premium on the user record. Implement checkout as a synchronous Stripe PaymentIntent flow through a thin standard-library HTTP client; on confirmed success, persist the premium tier and a payment-attempt record. Keep routes thin, business rules in services, and all errors in the required {detail, code} shape.

## Affected modules
- repo/app/routes/auth.py
- repo/app/routes/me.py
- repo/app/routes/checkout.py
- repo/app/services/auth_service.py
- repo/app/services/checkout_service.py
- repo/app/repositories/user_repository.py
- repo/app/repositories/session_repository.py
- repo/app/repositories/payment_repository.py
- repo/app/models/user.py
- repo/app/models/session.py
- repo/app/models/payment_attempt.py
- repo/app/schemas/auth.py
- repo/app/schemas/user.py
- repo/app/schemas/checkout.py
- repo/app/dependencies/auth.py
- repo/app/core/security.py
- repo/app/core/settings.py
- repo/tests/test_auth.py
- repo/app/tests/test_me.py
- repo/app/tests/test_checkout.py

## Data / API impact
- DB impact: yes
- API impact: yes
- Migration needed: yes

## Constraints for implementation
- Do not store plaintext passwords or raw Stripe secrets in the database.
- Keep all route handlers thin; validation and business rules belong in services.
- Use repository abstractions for all DB access.
- Use a bearer token auth dependency for /me and /checkout; /register and /login stay public.
- Do not add webhooks, OAuth, GraphQL, or WebSockets.
- Tests must use FastAPI TestClient with isolated temporary SQLite databases.
- Stripe calls must be isolated behind a service and mocked in tests.
- Preserve the required JSON error contract on every failure path.

## Risks
- Stripe payment confirmation is synchronous here; without webhooks, delayed/async settlement edge cases are not handled.
- Custom session-token handling increases security responsibility; token hashing and expiration need to be implemented carefully.
- Checkout requires a valid Stripe payment method input contract that the client must satisfy.
- Premium upgrade is tied to successful API confirmation only; later disputes/refunds are out of scope.

## Breaking changes
- none

## Sensitivity
- financial
- security
- pii
- compliance
