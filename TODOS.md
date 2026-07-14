# TODOS

Items accepted for future work but out of current PR scope.

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
