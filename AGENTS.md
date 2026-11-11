# Repository architecture rules for Codex

Every change must preserve a clear, repeatable architecture for both existing
brand deployments and future WhatsApp/Chatwoot brands created from this repository.

Before editing, Codex must evaluate the change in both scenarios:

1. a fresh isolated deployment created with `supabase/bootstrap.sql`; and
2. an existing deployment upgraded without losing its data.

Required design rules:

- Keep runtime code generic and select the brand through `BUSINESS_ID`; do not add
  brand-specific branches when an instruction file or configuration can express
  the behavior.
- Keep each brand's Railway, Supabase, Redis queue, Meta number and Chatwoot
  account/inbox isolated. Never solve one deployment by reusing another brand's
  IDs, secrets, data or queue.
- Whenever a runtime change requires a database column, constraint, index, role,
  bucket or policy, update the canonical `supabase/bootstrap.sql` for future
  deployments and the canonical `supabase/upgrade_existing_brand.sql` for existing
  ones. Use a narrowly scoped repair only when an operator cannot safely run the
  complete existing-brand upgrade.
- Keep runbooks synchronized with code and SQL. State whether a step applies to a
  new project, an existing project, or both.
- Add regression tests for cross-brand isolation and for fresh-install/upgrade
  parity. Avoid one-off fixes that only make the currently failing brand work.
- Preserve compatibility with Tanaka, Memo's, Velvet and a future arbitrary
  `BUSINESS_ID` unless a task explicitly requires a breaking migration.

- Authorized operator prototype: Tanaka/Velvet share CRM scope and one canonical actor with legacy owner aliases; bot management remains separate. Memo's retains one bot and two fixed queue actors on the existing login. Queue/alias claims come only from the authenticated frontend server; enforce queue access on conversation/media/command routes. Transfer uses the bot's atomic versioned owner operation with AI paused and unsettled-send checks, then changes CRM assignment; never emulate transfer through close/take or direct operational writes. Notifications revalidate queue/assignment and transition, suppress onboarding backlog and keep Admin opt-in. Canonical setup/upgrade SQL must match the incremental transfer script. See docs/CRM_OPERATOR_QUEUES.md.

- The visible CRM inbox tabs are Humano (default) and IA. Keep backend all/history for other consumers. Every list orders last real user/model/asesor messages descending with stable business/phone ties before pagination. Incoming-only unread counts use the canonical operator's acknowledged visible source-ID watermark, independent from control/membership. Pending-handoff time is optional detail data, not list ordering. Preserve confirmed returns, CRM-only historical dismissal and historical timestamps/audit. Notifications reuse the existing transition/outbox for new relevant inbound messages as well as handoffs/transfers; seed existing watermarks without history replay, enforce current queue/assignment, and use the same canonical actor in config/registration/payload. No new schema or variables are required.
