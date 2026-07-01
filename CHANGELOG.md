# Changelog

All notable changes to this project will be documented in this file.

## [0.0.1.0] - 2026-07-01

### Fixed

- **Telegram webhook**: graph object is now resolved lazily inside the background task instead of at scheduling time, preventing `AttributeError` crashes when the app starts before the graph is fully initialized
- **Telegram webhook**: if the graph is not available at message processing time, the bot now sends a user-facing "service unavailable" message instead of silently dropping the request
- **Triage node**: LLM responses wrapped in markdown code fences (`` ```json `` or `` ``` ``) are now correctly stripped before JSON parsing, preventing fallback routing failures when the model includes formatting in its response
- **Triage node**: fence stripping now uses regex instead of string split, handles uppercase language tags (`` ```JSON ``), and returns the Pydantic-validated decision value rather than the raw LLM string
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
