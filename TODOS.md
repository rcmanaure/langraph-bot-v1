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
- **`verify_operator_key` doc/code mismatch.** README says the operator key is
  `sha256(SECRET_KEY)`, but `app/auth.py:9-11` compares the raw header against
  `SECRET_KEY`/`OPERATOR_TOKEN` directly, not a hash of it. Found during
  /plan-ceo-review (Gmail/Drive OAuth plan, 2026-07-19) outside-voice pass —
  not a live bug (auth still works, docs are just wrong about the mechanism),
  but worth fixing before/alongside adding tenant-scoped operator auth
  (see `patient_index`/tenant-scoping work), since that work touches
  `app/auth.py` anyway.
- **"Sign in with Google" para operador individual (reemplaza `operator_identity` texto libre).**
  Qué: login individual del operador vía Google OAuth (reusa `google-auth`, ya
  agregado en el plan Gmail/Drive de `patient-results`), en vez de un campo de
  texto libre no verificado. Requiere agregar una capa de sesión/JWT al panel
  admin (`admin.html` hoy no tiene ninguna — solo una key estática en
  `localStorage`). Por qué: el `operator_identity` actual (D7 del plan
  `2026-07-19-gmail-drive-patient-results.md`) es spoofable — cualquiera con
  la key del tenant puede escribir el nombre que quiera en el audit trail de
  acceso a PHI. Contexto: surgido en `/plan-eng-review` 2026-07-20 (outside-voice
  Codex, hallazgo 4/13) — deliberadamente NO incluido en esa PR porque agregar
  sesión/JWT reabría el gate de complejidad (pasaba de 9 a ~11-12 archivos).
  Pros: identidad verificada criptográficamente por Google, cero dependencia
  nueva (reusa `google-auth`). Cons: agrega infraestructura de sesión que hoy
  no existe en el panel; dos flujos OAuth distintos a mantener claros (el del
  tenant conectando Gmail/Drive vs. el del operador logueándose) para no
  confundirlos en el código. Depende de: que el plan Gmail/Drive (T0a-T6) ya
  esté implementado y el panel tenga selector de tenant (T0a).
- **WhatsApp: `wa_service_windows` table has no reader.** It's updated on every
  inbound message but nothing enforces Meta's 24h free-form-reply window. Not a
  live bug today — every current send is a same-turn reply to an inbound message,
  so the window is always fresh — but it will matter the moment any
  business-initiated/proactive send path exists (e.g. an operator dashboard
  replying later, or a follow-up message). Add the read-side check (and a
  template-message fallback) when that feature is built.
