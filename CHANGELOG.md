# Changelog

All notable changes to this project will be documented in this file.

## [0.0.1.0] - 2026-07-01

### Fixed

- **Telegram webhook**: graph object is now resolved lazily inside the background task instead of at scheduling time, preventing `AttributeError` crashes when the app starts before the graph is fully initialized
- **Telegram webhook**: if the graph is not available at message processing time, the bot now sends a user-facing "service unavailable" message instead of silently dropping the request
- **Triage node**: LLM responses wrapped in markdown code fences (`` ```json `` or `` ``` ``) are now correctly stripped before JSON parsing, preventing fallback routing failures when the model includes formatting in its response
- **Triage node**: fence stripping now uses regex instead of string split, handles uppercase language tags (`` ```JSON ``), and returns the Pydantic-validated decision value rather than the raw LLM string

### Changed

- `docker-compose.yml`: updated service configuration
- `Dockerfile`: minor build layer adjustment

### Added

- Full test coverage for all triage fallback paths: clean JSON, fenced JSON (`` ``` ``), fenced JSON with `json`/`JSON` tag, invalid JSON fallback, unknown enum value fallback, and LLM error fallback
- Regression tests for the lazy graph access fix: early-return paths (no message, empty text, voice too large, STT failure) verified to not require a graph; normal text path verified to send a user-facing error when graph is unavailable
