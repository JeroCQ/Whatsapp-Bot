# CRM API connection recovery, November 20

Observed incident: central API /ready returned 503 and authenticated requests
logged SQLAlchemy QueuePool exhaustion (four connections, zero overflow, five
seconds checkout wait). The compatible API remained ready. Tanaka Supabase was
healthy, with 23 of 60 connections, nine chatty_runtime connections and no lock
waiters in the sampled query. This establishes API-pool starvation, not its
exact original retention cause; no claim that a missing migration was found.

Recovery restarted only chatty-pilot-api, not bots, workers or databases. The
API-specific factory now recycles connections after 120 seconds, enables TCP
keepalives with a 15-second TCP user timeout, disables automatic prepared
statements for pooler compatibility, and bounds statements/locks/idle transactions
at 15/2/30 seconds. The existing maximum of four connections and zero overflow
remains unchanged; this does not increase the database connection budget.
Workers and migration factories retain their old policy. Pool exhaustion returns
a sanitized 503 with one short log entry, without retrying any write/send.

Applies to existing and fresh API installations. No schema, RLS, secrets,
environment variables, business data or control-state changes. Deploy the two
CRM APIs only; keep the worker and all bots on their current releases. The
previous frontend Workers redirect repair remains required and unchanged.

No local app, tests, build or customer messages were run. Confirm deployment
SUCCESS, /ready and absence of new pool errors; those checks alone do not prove
an authenticated inbox or instruction/catalog save worked. Refresh the already
published UI and confirm reads before editing business content. Never resend an
uncertain WhatsApp message automatically or use an unknown result as delivery.
