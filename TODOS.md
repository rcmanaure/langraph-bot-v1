# TODOS

Items accepted for future work but out of current PR scope.

- **`search_audit.filters_used` stores patient names in plaintext.** Accepted
  phase-1 risk (lab-staff-search plan, Round 2 outside-voice finding) — audit
  needs to be human-readable for compliance review, and this is an internal
  tool, not a public surface. Sentry breadcrumbs/context are scrubbed
  defensively (`app/main.py:_scrub_lab_search_pii`), and no log/exception
  message anywhere in `drive.py`/`gmail.py`/`lab_search_handler.py` embeds raw
  filter text. Revisit if the lab's compliance posture requires encryption at
  rest for audit rows.
- **Lab staff search phase 2 (vision auto-match, fuzzy-match, image-as-
  trigger, `search_result_cache`)** — deliberately deferred, needs its own
  `/plan-ceo-review` + `/plan-eng-review` pass once phase 1 has real usage
  data. See the plan file's "Phase 2 backlog" section.
- **Google Workspace least-privilege folder/label scoping for the shared
  Drive/Gmail service credential** — admin-console task (share only the
  results folder with the connected account), not code. Do before granting a
  real lab tenant staff-secret access.

- **WhatsApp: `WhatsAppAdapter.normalize()` is unused by the live webhook path.**
  `whatsapp_webhook`/`_handle_message` (`app/channels/whatsapp.py`) parse the raw
  payload directly instead of going through the `ChannelAdapter` Protocol, unlike
  Telegram's text path. Already flagged in code as `ponytail: ... migrate when
  adding a 3rd channel` — deferred again rather than refactored under time
  pressure while getting WhatsApp ready for testing. Revisit when adding a 3rd
  channel or when unifying the two handlers' message-type branching.
- **Feedback de usuario 👍/👎 sobre respuestas del bot.** (movido desde plan
  voz/escalabilidad, eng-review 2026-07-06 D9 — no seleccionada en decisión CEO)
  Qué: botones inline (Telegram) / reacciones (WhatsApp) → tabla feedback.
  Por qué: señal directa de calidad de respuestas para mejorar RAG/prompts.
  Pros: datos de eval reales por tenant. Cons: UI por canal x2, tabla nueva,
  volumen bajo la hace poco útil al inicio. Contexto: implementar DESPUÉS de
  3.0 media_pipeline (evita duplicar handling por canal) y de 3.4 métricas
  (comparte patrón de agregación). Empezar por: callback_query handler en
  telegram.py + tabla `response_feedback`.
- **WhatsApp: `wa_service_windows` table has no reader.** It's updated on every
  inbound message but nothing enforces Meta's 24h free-form-reply window. Not a
  live bug today — every current send is a same-turn reply to an inbound message,
  so the window is always fresh — but it will matter the moment any
  business-initiated/proactive send path exists (e.g. an operator dashboard
  replying later, or a follow-up message). Add the read-side check (and a
  template-message fallback) when that feature is built.
