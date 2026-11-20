# Central CRM human-send regression, 2026-11-20

Applies to new central installations and existing central deployments of every
business. No schema, credential, source migration or historical-state edits.

- Central contexts intentionally have no pilot operator list. Only the opt-in
  pilot bridge applies that list. Central sending retains authenticated scope,
  business/channel validation and atomic owner, pause and version checks.
- Command receipts lock their command row, not the business-wide synchronization
  lock held during remote reads. Control projection writes lock the affected
  conversation. Return-to-AI locks source before projection to match sync order.
  Sync reloads classification under that projection row lock to preserve confirmed
  assignments, transfers and release markers.
- Read acknowledgements serialize only one operator's watermark. Repeated
  acknowledgements of the same watermark do not rewrite read time or add audit
  rows. They never change control, assignment or human-inbox membership.
- Both frontends retain successful watermarks across polling effect cleanup and
  permit only one acknowledgement in flight per conversation. Poll intervals and
  optimistic message reconciliation are unchanged.

Deploy the shared bot revision for all businesses and the CRM APIs and sole active
sync worker. Keep the old commercial worker frozen. Publish each frontend through
its existing Lovable flow. Preserve Meta webhooks, queues and source databases.

Manual check with the owner's test number only: open the conversation, take
control, wait for confirmed ownership, send one uniquely identifiable message,
and compare one receipt on WhatsApp with one CRM bubble. Read/refresh repeatedly:
no unread badge should return without a new incoming message. Return to AI and
verify subsequent inbound messages are processed normally. Do not replay any
older uncertain command; use only its existing command-status lookup.

No local tests, builds, application execution or real-message probes were run.
Service readiness is not proof of end-to-end WhatsApp delivery.
