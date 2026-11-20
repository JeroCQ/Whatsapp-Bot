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

## Follow-up: status lookup racing dispatch

An automatic status GET could see the durable `checking` command before the
original request claimed forwarding. Missing bot receipt recovery then replaced
`checking` with `unknown`; the original request returned without dispatching.
Control ownership was valid, but no message reached the bot.

Recovery observations no longer overwrite checking/forwarding or downgrade a
terminal receipt. Only a still-checking original request may claim forwarding.
Both frontends defer status polling while posting and allow one in-flight GET
per command. Repeated identical results no longer append duplicate audit rows.

An old unknown message can become `not_forwarded` only with its durable requested
audit and no committed forwarding audit, checked under its command row lock. The
forwarding audit is committed before any bot POST. A missing bot receipt alone
remains insufficient. This closes the old command without replay, allowing a new
explicit send. No delivery is fabricated; genuinely dispatched unknown sends stay
unknown. No schema/variables/bot restart is needed for this follow-up: update the
two CRM APIs and publish the frontends through their existing Lovable flow.
