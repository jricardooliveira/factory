# ADR-US-0001: Email/password accounts with Stripe premium upgrade

- Date: 2026-06-03
- Story: US-0001
- Status: proposed (pending architecture sign-off)

## Decision
Add a minimal auth + billing flow within the existing backend layers: registration stores an email plus a one-way password hash; login issues an opaque session token backed by a server-side session record; checkout creates a Stripe payment session and records it as pending; a Stripe webhook finalizes the payment and flips the user to premium. Task creation should consult the user tier on each request and enforce separate free/premium limits through one shared entitlement check.

## Affected modules
- backend/auth
- backend/users
- backend/database/migrations
- backend/database
- backend/payments
- backend/webhooks
- backend/tasks
- backend/authorization
- backend/config

## Data / API impact
- DB impact: yes
- API impact: yes
- Migration needed: yes

## Constraints for implementation
- Never store raw passwords or raw Stripe payment data; only hashed passwords and Stripe IDs/metadata.
- Webhook must be the source of truth for marking premium, not the client redirect/success page.
- All checkout and webhook handlers must be idempotent to tolerate retries and duplicate Stripe events.
- Task limit checks must happen at the task creation boundary, not in the UI.
- Keep the auth token format opaque and server-validated so it can be revoked if needed.

## Risks
- Duplicate Stripe webhook events could grant premium twice unless idempotency is enforced.
- If premium is granted on the client return path instead of the webhook, users could spoof success.
- Concurrent task creation requests could bypass limits without a transactional or atomic count check.
- Password hashing settings that are too slow or too weak can hurt either UX or security.
- If existing anonymous task creation is currently allowed, adding auth/limits will be a breaking behavior change.

## Breaking changes
- Task creation will require an authenticated session token; anonymous callers will no longer be able to create tasks.
- User records gain new auth/billing fields and may need backfill defaults for existing accounts.

## Sensitivity
- security
- financial
- pii
- compliance
