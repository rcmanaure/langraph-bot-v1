# ADR-004: Per-Channel Webhook Authentication

**Status:** Accepted  
**Date:** 2025-01

## Context

Both Telegram and WhatsApp webhooks are public HTTP endpoints. Without authentication, any actor knowing the URL can inject arbitrary messages into the graph.

## Decision

Use the authentication mechanism **provided by each platform**:

- **Telegram:** `x-telegram-bot-api-secret-token` header (pre-shared secret set via `setWebhook`). Compared with `hmac.compare_digest` to prevent timing attacks.
- **WhatsApp Cloud API:** `x-hub-signature-256` header (HMAC-SHA256 of the raw request body, keyed with the app secret). Verified before body parsing.

Each tenant stores its own secret in the `tenants` table. Secrets are compared per-request; no caching.

The `ChannelAdapter.verify(request)` Protocol method is the enforcement point — all adapters must implement it (see `app/channels/base.py`).

## Alternatives Considered

| Option | Why Rejected |
|---|---|
| IP allowlist (Telegram/Meta CIDR ranges) | IP ranges change without notice; brittle in cloud NAT/proxy setups |
| No authentication | Unacceptable: any actor can replay messages, inject prompt attacks, or exhaust query quotas |
| Shared global secret | Single secret means one leaked tenant credential compromises all tenants |
| mTLS | Telegram and WhatsApp don't support client certificates on their webhook push |

## Consequences

**Positive:**
- Per-tenant secrets: compromising one tenant's secret does not affect others
- Timing-safe comparison: `hmac.compare_digest` prevents oracle attacks
- WhatsApp HMAC covers the raw body: payload tampering is detectable

**Negative:**
- Webhook secret rotation requires updating both the DB row and re-calling `setWebhook` (Telegram) or the Meta app dashboard (WhatsApp)
- Telegram `secret_token` is limited to 1–256 characters, `[A-Za-z0-9_-]` only — must be validated on tenant creation
- If `app_secret` is not configured for a WA tenant, verification is skipped (permissive mode); this must be flagged in the admin panel
