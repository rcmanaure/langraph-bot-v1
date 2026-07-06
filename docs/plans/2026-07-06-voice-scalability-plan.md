# Plan: Voz robusta + Escalabilidad + Features

**Fecha:** 2026-07-06 · **Branch:** feat/memory-bot · **Estado:** PROPUESTO (no implementado)

## Hallazgo principal

**Las notas de voz con Groq YA están implementadas** en ambos canales:

- `app/services/stt.py` — `transcribe()` con Groq `whisper-large-v3` (API OpenAI-compatible, sin dependencia extra).
- `app/channels/telegram.py:157-176` — branch `voice`: valida tamaño, descarga, transcribe, inyecta al graph.
- `app/channels/whatsapp.py:263-277` — branch `audio`/`voice`: valida tamaño vía metadata (sin descargar), transcribe, inyecta al graph.

El plan por tanto NO es "agregar voz" — es **endurecer la voz existente**, escalar la arquitectura y agregar features.

---

## Fase 1 — Voz robusta (quick wins, ~1 sesión CC)

### 1.1 Eliminar fallos silenciosos (CRÍTICO)
Hoy, si Groq falla o la transcripción sale vacía, el bot **no responde nada**:

- `telegram.py:166-170`: `except → logger.warning → return` y `if not text_content: return`.
- `whatsapp.py:276-278`: igual.
- `stt.py:15-17`: sin `GROQ_API_KEY` → devuelve `""` → silencio total.

**Fix:** en cada path de fallo, enviar al usuario un mensaje claro:
- STT falló: "No pude procesar tu nota de voz. ¿Puedes escribirme tu consulta?"
- Transcripción vacía: "No escuché nada en el audio. ¿Puedes repetirlo o escribirme?"
- API key ausente: mensaje "función no habilitada" + `logger.error` (no warning).

### 1.2 Hint de idioma español
`whisper-large-v3` sin `language` autodetecta — más lento y menos preciso. Pasar `language="es"` (configurable por settings: `stt_language: str = "es"`). Opcional: `prompt` con vocabulario del dominio (nombres de exámenes médicos) para mejorar precisión en términos técnicos.

### 1.3 Reusar cliente Groq
`stt.py:18` crea `AsyncOpenAI` en cada llamada. Crear una vez a nivel módulo (lazy singleton, igual patrón que `services/llm.py`).

### 1.4 Telegram: cubrir `audio` y `video_note`
Telegram distingue `voice` (nota de voz), `audio` (archivo de audio) y `video_note` (video circular). Hoy solo `voice` se procesa; `audio` cae al branch de texto y se ignora. WhatsApp ya cubre ambos tipos. Unificar.

**Correcciones (eng-review D10a):** `stt.py:21` hardcodea `"audio/ogg"` — pasar mime type real por tipo (`voice`=ogg, `audio`=mp3/m4a según `mime_type` del payload, `video_note`=mp4; Whisper acepta mp4). Y `TelegramAdapter.normalize()` (`telegram.py:121`) solo filtra `"voice"` — agregar `audio`/`video_note` al gate o se doble-procesan. Tests por formato: ogg, mp3, mp4.

### 1.5 Feedback inmediato al recibir audio
El `sendChatAction: typing` de Telegram se envía DESPUÉS de la transcripción (`telegram.py:222-229`). STT puede tardar 2-5s → usuario sin señal. Mover el typing indicator ANTES de descargar/transcribir. WhatsApp ya lo hace bien (`_mark_read_and_typing` primero).

### 1.7 Hotfix: dedup Telegram colisiona entre tenants (eng-review D2)
`telegram.py:27` — `_is_duplicate(update_id)` keyed solo por `update_id`, que es secuencial POR BOT. Dos tenants pueden compartir update_id → mensaje de un tenant descartado como duplicado del otro. Fix inmediato (1 línea): key `f"{tenant_slug}:{update_id}"`. La versión Postgres (2.1) hereda la key compuesta.

### 1.6 Tests
- STT falla → usuario recibe mensaje de error (ambos canales).
- Transcripción vacía → usuario recibe mensaje (ambos canales).
- `language="es"` pasado al cliente.
- Telegram `audio` type procesado.
- `GROQ_API_KEY` ausente → mensaje "no habilitado" + `logger.error`.
- Dedup: 2 tenants con mismo `update_id` → ambos mensajes procesados (regresión D2).

---

## Fase 2 — Escalabilidad / profesionalización (~2 sesiones CC)

### 2.1 Estado en memoria bloquea multi-worker (CRÍTICO para escalar)
Tres estructuras viven en RAM del proceso:

| Estructura | Archivo | Problema con >1 worker/réplica |
|---|---|---|
| `_SEEN_UPDATES` (dedup Telegram) | telegram.py:23 | Duplicados procesados 2x → doble respuesta al usuario |
| `_SEEN_WA` (dedup WhatsApp) | whatsapp.py:29 | Igual |
| `InMemoryCache` (cache de nodos) | builder.py:87 | Cache misses cruzados, inofensivo pero inútil |

**Fix propuesto:** dedup en Postgres con `INSERT ... ON CONFLICT DO NOTHING` sobre tabla `processed_messages (tenant_slug, channel, message_id, seen_at)` — **key compuesta con tenant** (eng-review D2, evita colisión de update_id entre bots) + índice único sobre la key + limpieza por scheduler (borrar >24h). Sin Redis nuevo — reusa infra existente. El checkpointer/store ya son Postgres (correcto según docs LangChain: AsyncPostgresSaver es el patrón de producción).

**Tests 2.1:** carrera concurrente (2 inserts simultáneos misma key → 1 procesa) [E2E], cleanup del scheduler borra >24h, tenants distintos con mismo message_id no colisionan.

### 2.2 BackgroundTasks no es durable (decisión arquitectónica)
Docs LangChain (engine-webhooks best practices): "Acknowledge fast. Move slow work onto a queue." Hoy `background_tasks.add_task()` corre en el mismo proceso: un deploy/crash a mitad de un turno LLM **pierde el mensaje del usuario sin retry ni registro**.

**DECIDIDO (2026-07-06): Opción B — Cola Postgres (`procrastinate`).** Durable, retry, sin infra nueva. Workers separados del proceso web → desbloquea multi-worker real junto con 2.1.

Alternativas descartadas: A (aceptar riesgo — descartado, se quiere durabilidad), C (Redis+Celery — infra extra innecesaria).

**Spec worker bootstrap (eng-review D3):** extraer factory compartida `app/runtime.py::build_runtime() → (pool, checkpointer, store, graph)`. El lifespan de FastAPI (`main.py:79-114`) y el entrypoint del worker procrastinate llaman la MISMA factory — un solo lugar para tuning de pool, cero duplicación del setup crítico.

**Spec de diseño obligatoria (eng-review D7 — escribir ANTES de implementar):**
1. **Idempotencia de turno:** un task que crashea después de `adapter.send()` NO debe re-enviar en el retry. Registrar `sent_at` por (thread_id, turno) antes del send; retry verifica y salta el send si ya salió.
2. **Interrupts (HITL):** un run que cae en `interrupt_node` termina sin respuesta — definir cómo el resume del operador fluye por el worker (mismo task queue, task nuevo `resume_turn`).
3. **Evaluar `queueing_lock` de procrastinate ANTES de construir `processed_messages`:** el lock nativo por key puede dar dedup tenant-scoped gratis → la tabla propia de 2.1 podría ser redundante. Si aplica, 2.1 se reduce al hotfix de Fase 1.7 + lock de cola. Si no, dedup INSERT y defer del job DEBEN compartir transacción (evita mensaje perdido si el defer falla tras commitear dedup).
4. **Tareas periódicas:** migrar `expire_interrupts` (APScheduler in-process, `app/scheduler.py:13`) y el cleanup de dedup a periodic tasks de procrastinate — con N réplicas el scheduler in-process corre N veces concurrente.
5. **Timeout 2.4 dentro del task:** timeout → mensaje de error al usuario Y task marcado como fallido SIN retry automático (evita "error" seguido de la respuesta real duplicada).

**Tests 2.2:** task reintenta tras fallo transitorio, retry post-send NO re-envía (idempotencia), worker bootstrap construye graph funcional vía factory, mensaje encolado sobrevive restart del worker [E2E], resume de interrupt fluye por worker, timeout no dispara retry.

### 2.3 Cifrar secretos de Telegram en reposo
Inconsistencia: WhatsApp cifra `wa_access_token`/`wa_app_secret` con Fernet; Telegram guarda `bot_token`/`webhook_secret` en claro (`telegram.py:262-264` los lee directo). Migración: cifrar columnas de Telegram con el mismo patrón + migración Alembic de datos existentes.

**Touchpoints a enumerar (eng-review D10b):** el webhook lee secretos vía SQL crudo en hot path (`telegram.py:262-265`) → descifrar por request; `set_webhook`/`delete_webhook` necesitan token plaintext (`telegram.py:36-63`); `app/routes/admin.py` escribe/lee tokens en CRUD de tenants. Todos los lectores/escritores cambian, no solo la columna.

**Tests 2.3:** roundtrip cifrado/descifrado, migración procesa filas legacy en claro, webhook funciona post-migración, setWebhook recibe plaintext descifrado, admin CRUD cifra al escribir.

### 2.4 Timeout global por turno
`graph.ainvoke()` sin timeout — un LLM colgado retiene el background task indefinidamente. Envolver en `asyncio.wait_for(..., timeout=90)` con mensaje de error al usuario.

**Tests 2.4:** timeout dispara → usuario recibe mensaje de error, no silencio.

### 2.5 Deuda ya registrada en TODOS.md (mantener deferred)
- `WhatsAppAdapter.normalize()` sin uso — migrar al agregar 3er canal.
- `wa_service_windows` sin lector — necesario ANTES de cualquier envío proactivo (Fase 3.3 depende de esto).

---

## Fase 3 — Features nuevas

**DECIDIDO (2026-07-06, revisado en eng-review):** 3 features en scope. Orden: 3.0 media pipeline → 3.2 PDF (S-M, reusa vision) → 3.3 Proactivos WA (M, depende de 2.5) → 3.4 Métricas (M).
- **3.1 TTS DESCARTADA (eng-review D8):** Groq PlayAI TTS solo inglés/árabe — sin voces en español, proveedor inviable para este bot. Reabrír solo si se adopta otro vendor (OpenAI TTS, ElevenLabs).
- **3.5 Feedback 👍/👎 → TODOS.md (eng-review D9):** no fue seleccionada en la decisión CEO; movida a TODOS.md con contexto.

### 3.0 Prerequisito: extraer `app/services/media_pipeline.py` (eng-review D4)
La lógica de media está duplicada entre canales (voz: `telegram.py:157-176` ≈ `whatsapp.py:263-277`; imagen: `telegram.py:177-207` ≈ `whatsapp.py:279-312`). TTS y PDF en ambos canales duplicarían cada feature 2x. Extraer pipeline única: `(bytes, media_type, caption) → texto para el graph | error usuario`. Los canales solo descargan bytes y envían respuestas. TTS/PDF se implementan UNA vez. Tests: pipeline por tipo de media + errores; canales solo se testean como adaptadores.

### 3.1 Respuesta de voz (TTS) — el bot contesta con audio
Groq ofrece TTS (PlayAI). Si el usuario manda voz, responder con voz + texto. Simetría conversacional. Esfuerzo: M. Config por tenant (`tts_enabled`).

### 3.2 Soporte PDF
Hoy `document` en WhatsApp se rechaza con mensaje (`whatsapp.py:313-320`). Convertir PDF→imagen (pypdfium2) → pipeline vision existente. **Solo primera página** (eng-review D6): caso de uso real es orden médica de 1 página; memoria/costo acotados; si vision da UNCERTAIN, pedir foto. Esfuerzo: S-M.

**Tests 3.x (escribir junto a cada feature):** PDF 1 página → query extraída; PDF multipágina → solo pág. 1; PDF corrupto → mensaje error. Proactivos: ventana <24h → free-form, >24h → template. Métricas: contadores incrementan por evento, endpoint admin agrega por tenant.

### 3.3 Envíos proactivos + enforcement ventana 24h WhatsApp
Requiere el lector de `wa_service_windows` (2.5) + fallback a template messages. Desbloquea: seguimientos, recordatorios, respuestas de operador tardías. Esfuerzo: M.

### 3.4 Métricas de negocio por tenant
Conteo de conversaciones, transcripciones STT, tokens/costo estimado por tenant → tabla + endpoint admin. Base para facturación real de los planes (`PLAN_LIMITS` ya existe). Esfuerzo: M.

---

## Orden recomendado (con decisiones aplicadas)

```
Fase 1 (voz robusta + hotfix dedup)  →  2.3 cifrado TG + 2.4 timeout  →  2.2 diseño+cola procrastinate (absorbe 2.1 si queueing_lock aplica)  →  3.0 media pipeline → 3.2 PDF → 3.3 proactivos → 3.4 métricas
        ~1 sesión                              ~1 sesión                                  ~1-2 sesiones                                                    ~1 sesión c/u
```

## Qué ya existe (reuso mapeado)
- STT Groq (`app/services/stt.py`) — se endurece, no se reconstruye.
- Pipeline vision (`app/services/vision.py`) — PDF lo reusa tras render de página.
- Checkpointer/Store Postgres (`main.py:93-104`) — patrón correcto según docs LangChain; la factory D3 lo extrae, no lo reemplaza.
- Fernet (`app/crypto.py`) + patrón WhatsApp — 2.3 lo replica para Telegram.
- `wa_service_windows` (escritura ya existe) — 3.3 agrega solo el lector.
- `PLAN_LIMITS` (`config.py:80`) — 3.4 construye la medición que le falta.

## NO en alcance
- **TTS respuesta con voz (3.1)** — descartada: Groq PlayAI sin voces en español; reabrir solo con otro vendor.
- **Feedback 👍/👎 (ex-3.5)** — movida a TODOS.md; no seleccionada en decisión CEO.
- Migrar a LangGraph Platform / Agent Server (infra gestionada — reevaluar si el volumen lo justifica).
- Reescritura del handler WhatsApp hacia el Protocol (deferred en TODOS.md, se mantiene).
- Streaming de tokens (no aplica a canales de mensajería con mensaje único).

## Modos de fallo críticos (test + handling + visibilidad)
| Path nuevo | Fallo realista | Test | Handling | Usuario ve |
|---|---|---|---|---|
| STT | Groq 500/timeout | ✓ (1.6) | ✓ mensaje | error claro |
| Dedup PG | carrera 2 workers | ✓ (2.1) | ON CONFLICT | nada (correcto) |
| Cola | crash post-send → retry | ✓ (2.2) | idempotency key | 1 sola respuesta |
| Cola | defer falla tras dedup commit | ✓ (2.2) | misma transacción / queueing_lock | mensaje NO perdido |
| Timeout | LLM colgado | ✓ (2.4) | wait_for + sin retry | error claro, sin duplicado |
| PDF | corrupto / multipágina | ✓ (3.2) | try + mensaje | error claro |
Ningún gap crítico restante: cada path tiene test + handling + error visible.

## Paralelización (worktrees)
| Paso | Módulos | Depende de |
|---|---|---|
| Fase 1 | channels/, services/stt | — |
| 2.3 cifrado | models/, routes/admin, channels/telegram | — |
| 2.4 timeout | channels/ | — |
| 2.2 cola | nuevo worker, app/runtime.py, scheduler | Fase 1 (hotfix dedup) |
| 3.0-3.4 | services/, channels/ | 2.2 |

Lanes: **A:** Fase 1 → 2.2 (secuencial, comparten channels/). **B:** 2.3 (independiente, paralelo a Fase 1). 2.4 entra en lane A (channels/). Fase 3 secuencial tras 2.2. Conflicto flag: 2.3 y Fase 1 tocan ambos `channels/telegram.py` — coordinar o secuenciar.

## Tareas de implementación
- [x] **T1 (P1, CC ~5min)** — telegram — hotfix dedup key `slug:update_id` + test regresión (D2)
- [x] **T2 (P1, CC ~1 sesión)** — fase 1 — errores STT visibles + language=es + mime types + audio/video_note + typing temprano + tests (D10a)
- [x] **T3 (P2, CC ~30min)** — runtime — extraer `app/runtime.py::build_runtime()` (D3)
- [ ] **T4 (P1, CC ~1h)** — diseño 2.2 — escribir spec: idempotencia, interrupts, queueing_lock vs processed_messages, periodic tasks, timeout-sin-retry (D7)
- [ ] **T5 (P2, CC ~1 sesión)** — cola — implementar 2.2 según spec T4
- [ ] **T6 (P2, CC ~45min)** — seguridad — cifrado Telegram + touchpoints enumerados + tests (D10b)
- [ ] **T7 (P2, CC ~15min)** — channels — timeout `wait_for` en ambos webhooks + test (2.4)
- [ ] **T8 (P2, CC ~30min)** — services — extraer `media_pipeline.py` (D4)
- [ ] **T9 (P3, CC ~1 sesión)** — features — PDF primera página (D6) → proactivos → métricas, con tests 3.x

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/plan-ceo-review` | Scope & strategy | 1 | CLEAR (lightweight) | 2 decisiones: cola procrastinate, 4 features F3 |
| Codex Review | `/codex review` | Independent 2nd opinion | 1 | ISSUES ABSORBED (claude subagent) | 8 hallazgos, 4 sustanciales → D7-D10 |
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 1 | CLEAR | 9 issues, 0 critical gaps abiertos |
| Design Review | `/plan-design-review` | UI/UX gaps | 0 | — | n/a (sin UI) |
| DX Review | `/plan-devex-review` | Developer experience gaps | 0 | — | n/a |

- **CROSS-MODEL:** voz externa detectó el gap real de 2.2 (idempotencia/interrupts/queueing_lock) que la review interna specceó solo parcialmente (bootstrap). Absorbido como spec obligatoria D7. TTS descartada por inviabilidad de proveedor (D8).
- **VERDICT:** ENG CLEARED — plan listo para implementar (T1-T9). CEO review previa absorbida.

NO UNRESOLVED DECISIONS
