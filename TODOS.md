# TODOS

Items accepted for future work but out of current PR scope.

## Design Debt

### Typography refresh
**What:** Replace `font-sans` (system-ui / OS default) with a real typeface across the admin panel.
**Why:** system-ui is AI slop pattern #11 — the "I gave up on typography" signal. Flagged by /plan-design-review 2026-06-30. Admin panel is an internal tool but operators use it daily; legibility and perceived quality matter.
**How:** One `<link>` tag in `<head>` (Inter or Geist from Google Fonts CDN) + `fontFamily` in Tailwind config override.
**Effort:** ~15min CC. Zero risk.
**Depends on:** Nothing.


## Product Debt

### WhatsApp auto-webhook registration
Auto-register WA webhook when wa_phone_number_id + wa_access_token are set on a tenant. Requires WA Business API knowledge. Deferred from CEO plan scope.

### Soft-delete / tenant recovery
Deactivated tenants can be reactivated (PATCH active=true). Hard-deleted tenants cannot. Soft-delete would add a recovery path. Deferred from CEO plan scope.

### Migrate all admin.py raw SQL to ORM
admin.py uses raw SQL for create/read operations, ORM only for write operations on WA fields (encryption requirement). Full ORM migration deferred — comment in admin.py explains the split.

## User Memory Debt

### Cross-channel profile linking
**What:** When a user messages on both WhatsApp AND Telegram, they have two separate `user_profiles` rows. No mechanism exists to recognize them as the same person.
**Why:** Repeat lab customers may switch channels (WhatsApp at home, Telegram at work). Without linking, the bot treats them as strangers on the second channel. Profile data (name, past topics) is siloed.
**Pros:** Unified customer profile; personalization works regardless of channel; better operator analytics (true unique user count).
**Cons:** Complex identity design — requires careful handling to avoid leaking cross-channel identity (user may not want channels linked). Needs a `canonical_user_id` + `identity_links` table design.
**Context:** T1-T12 (per-user memory feature) implemented user_profiles with PK (tenant_id, user_id, channel). Cross-channel linking was explicitly deferred as Phase 2 during CEO review 2026-06-30. Start here: design the canonical_user_id scheme first; phone-number matching for WA↔TG is one approach but requires opt-in.
**Depends on:** T1-T12 shipped and stable in production.

### User-facing profile disclosure
**What:** When a user asks "¿Qué sabes de mí?" the bot has no way to answer. It knows their name and past topics but has no handler to disclose this.
**Why:** Transparency and trust. Users who feel recognized deserve to know what data is held. Also GDPR-adjacent: right to know what personal data is stored.
**Pros:** User trust signal; simple to implement (triage → new "profile_inquiry" decision → load_profile + format response).
**Cons:** Reveals data collection to users who may not have known. Low urgency for Venezuelan lab market — no regulatory pressure currently.
**Context:** The `user_profiles` table stores display_name + past_topics per user. A disclosure response would simply format these fields. The triage node would need a new decision class ("profile_inquiry"). The generate node would format the response using the profile data directly.
**Depends on:** T1-T12 shipped.
