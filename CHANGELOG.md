# Changelog

All notable changes to this project will be documented in this file.

## [0.0.2.0] - 2026-08-03

### Added

- **LLM observability via Phoenix**: self-hosted, open-source LLM tracing replaces LangSmith. Set `OBSERVABILITY_ENABLED=true` to turn it on (off by default); a boot-time self-check confirms message content is actually redacted from traces before tracing goes live against real traffic, and stays soft (logs a warning, keeps booting) if Phoenix simply isn't reachable
- **Cloudflare Quick Tunnels wired into `docker-compose.dev.yml`**: `cloudflared-api` and `cloudflared-phoenix` services auto-expose the local API and Phoenix UI over a `trycloudflare.com` URL — read it from `docker compose logs cloudflared-api` — no separate manual tunnel step needed for dev

### Fixed

- **Dev environment**: `docker-compose.dev.yml`'s `api` service skipped `alembic upgrade head` (it overrode the image's normal startup command), so a fresh dev database crashed on first boot with `UndefinedTableError`. Migrations now run before the server starts, matching production behavior
- **Admin panel**: the operator-key instructions in the README told you to SHA256 your `SECRET_KEY` before pasting it in — the server actually expects the raw value. Docs corrected; this was blocking first-time login for anyone following the README exactly
- **Admin panel**: tenant slugs had no format validation, so a slug like `"My Tenant!"` could be created successfully and then silently fail Telegram webhook registration (the slug is used unescaped in the webhook URL). Slugs are now restricted to lowercase letters, digits, and hyphens
- **Telegram webhook**: a malformed or non-object JSON body (with a valid tenant + secret) returned HTTP 500 instead of `{"ok": true}`, which meant Telegram would retry the same bad update forever instead of it being dropped like every other invalid-update case
- **Phoenix tracing redaction gap**: `OPENINFERENCE_HIDE_INPUT_TEXT`/`HIDE_OUTPUT_TEXT` alone didn't mask the raw serialized `input.value`/`output.value` span attributes populated on LLM-kind spans, leaking full unmasked content there even with redaction "on". `HIDE_INPUTS`/`HIDE_OUTPUTS` added to close it
- **Phoenix version pin**: the pinned Phoenix server image was too old for `get_spans` queries used in integration tests; re-pinned to `13.15.0` in both compose files

### Changed

- Observability backend switched from LangSmith to Phoenix — `LANGCHAIN_API_KEY`/`LANGCHAIN_PROJECT`/`LANGSMITH_HIDE_INPUTS`/`LANGSMITH_HIDE_OUTPUTS` removed from `.env.example` and settings; replaced by `OBSERVABILITY_ENABLED`, `PHOENIX_SECRET`, `PHOENIX_ADMIN_PASSWORD`

## [0.0.1.0] - 2026-07-01

### Fixed

- **Telegram webhook**: graph object is now resolved lazily inside the background task instead of at scheduling time, preventing `AttributeError` crashes when the app starts before the graph is fully initialized
- **Telegram webhook**: if the graph is not available at message processing time, the bot now sends a user-facing "service unavailable" message instead of silently dropping the request
- **Triage node**: LLM responses wrapped in markdown code fences (`` ```json `` or `` ``` ``) are now correctly stripped before JSON parsing, preventing fallback routing failures when the model includes formatting in its response
- **Triage node**: fence stripping handles all markdown code fence variants — bare `` ``` ``, `` ```json ``, and `` ```JSON `` — and returns the Pydantic-validated decision value rather than the raw LLM string
- **WhatsApp webhook**: HMAC signature verification now rejects requests where the `x-hub-signature-256` header is absent when `app_secret` is configured (previously a missing header bypassed the check entirely)
- **WhatsApp webhook**: verify-token endpoint now uses `hmac.compare_digest` instead of `!=` to prevent timing oracles
- **WhatsApp webhook**: malformed JSON payloads from Meta now return `{"ok": true}` instead of HTTP 500 (which would have triggered infinite Meta retries)
- **WhatsApp webhook**: `msg['from']` hard key access replaced with `.get()` + early return to prevent unhandled `KeyError` for system events in background tasks
- **WhatsApp webhook**: graph unavailability now handled with an explicit null check and user-facing message, matching the Telegram channel behavior
- **Telegram webhook**: empty STT transcription result now returns early before graph invocation, matching the WhatsApp channel guard
- **WhatsApp decrypt**: fallback to raw value on decrypt failure now logs an error (previously silent, making key rotation breakage invisible)

### Changed

- `docker-compose.yml`: Docker network renamed from `app` to `lgbot-net` — run `docker network create lgbot-net` (one-time) when updating an existing deployment
- `Dockerfile`: `chmod +x /app/entrypoint.sh` added to the build so the container image always ships an executable entrypoint regardless of the host filesystem mode

### Added

- Full test coverage for all triage fallback paths: clean JSON, fenced JSON (`` ``` ``), fenced JSON with `json`/`JSON` tag, invalid JSON fallback, unknown enum value fallback, and LLM error fallback
- Regression tests for the lazy graph access fix: early-return paths (no message, empty text, voice too large, STT failure) verified to not require a graph; normal text path verified to send a user-facing error when graph is unavailable
