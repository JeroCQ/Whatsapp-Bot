# Central migration completion and latency follow-up

The active migration is `chatty_20261120_central_02`, committed. Tanaka Supabase
`tbvcvqddpppqlwuehdaf` is the sole active database authority. Tanaka keeps its
original public tables; other businesses use their `chatty_<business>_` prefixes.
CRM data uses private schema `chatty_crm`. Original databases, files and IDs remain
preserved; Briosos was explicitly authorized to start with empty operational data.
Never re-run the old snapshot/install or resume the commercial sync worker.

Both CRM APIs serve this same central CRM with their existing JWT/account
compatibility. `chatty-pilot-sync` is the sole active central synchronizer.
The four existing bot/Redis/Meta sender deployments remain the sole consumers
and senders. They are not restarted for these CRM-only performance changes.

## Changes for both fresh and upgraded installations

- Fetch unread watermarks/counts for the authorized inbox page in one grouped SQL
  query, preserving the exact business/contact/operator keys and incoming-only
  semantics. Previously each contact needed two separate database round trips.
  Details use the same single-query aggregate.
- Unchanged authenticated operator metadata needs no advisory profile lock or
  UPDATE. JWT validation still runs on every request. Membership/queue/alias
  changes and retirement/subscription revocation still run under the write path;
  there is no credential or authorization cache.
- Prefetch each bounded sync message page's existing messages and conversations
  in two tenant-scoped queries. Keep ORM identities alive only for that page.
  Preserve every existing upsert, edit, receipt projection, cursor and read
  budget check. No historical messages are skipped to achieve the speed-up.
- Preserve all previous command dispatch/recovery, owner/pause/version, read
  acknowledgement and UI reconciliation fixes. Never replay uncertain sends.

No schema changes, new variables, new services, password changes, polling-rate
increases, queue changes, follow-up changes or migrations are needed.

Before this release, live inbox requests averaged about 6.1 seconds and a
historical sync page used the entire 15-second read budget. Source-level changes
reduce redundant network round trips; report observed post-release timings only
when actual authenticated traffic exists. Anonymous readiness is not an inbox or
WhatsApp latency measurement.

Regression cases were added to the existing API/sync tests but not executed,
per the no-local-tests/builds/application-execution constraint. Read-only SQL
comparison of 50 live contacts found identical old/new unread counts/watermarks.
Deployment verification covers terminal Railway SUCCESS, both ready endpoints,
registry modes, absence of duplicate central consumers and sync errors.

Optional owner-device check: refresh the inbox, open the owner's test contact,
send one unique message while confirmed human owner, and confirm one receipt on
WhatsApp plus one CRM bubble. Send a new inbound message from the same test phone
and verify its appearance without refreshing. Do not test with real customers.
