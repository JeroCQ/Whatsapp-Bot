# Chatty · Turn 2 · Briosos / +573105338124 only

**NOT APPLIED. Pilot inactive. Not executed or runtime-validated.**
No tests, apps, linters, typechecks, builds, browsers, Docker, workers, SQL or
provider calls were run. No secrets, live resources, webhook, branch bindings or
deployment workflows were changed. This is an implementation for manual review,
not evidence that a pilot is running. Turn 3 / historical commercial migration
is excluded.

## Inspected baselines and missing deployment evidence

Both existing feature branches are `feat/chatty-central-config-20261118`.
At inspection each HEAD matched its fetched feature ref; no later feature work
was discarded, rebased or rewritten.

- Whatsapp-Bot: `cc9e47c189077b1b599c75b23488ad76ad2a97ba`, direct child of
  `582ac58ee1e202b59c86fc4a0ae4645f31d0f88f`. The latter is the active CRM API
  commit supplied by the user, **not verified against a running service**.
  It lacks turn-1 central configuration/usage (0005) and this turn's pilot (0006).
- Frontend: `5140b2d64ba59e66d3640939ac50ff81c241486f`, direct child of
  `43678888e99713ba6640b60cd09a2da5a70e326d`. Turn-1 Admin UI is preserved.
- Active Briosos bot commit: **BLOCKED / unknown**. Active CRM worker commit:
  **BLOCKED / unknown**. Do not assume either contains current SQL/control fixes.
- Backend's only repository workflow is manual `workflow_dispatch` catalogue
  recovery; no push trigger. Frontend has no `.github` deployment workflow;
  Lovable follows `main`. Known documented service sources are legacy branches /
  `feat/crm-media-delivery`, not this feature branch. No Railway connection is
  available to verify current live bindings; pushing the existing feature branch
  does not change those bindings in this implementation.

## Exact targets and commands; do not substitute production staging services

| Role | Target and host | Source / root / command |
|---|---|---|
| Existing sole Briosos bot + queue consumer | Reported service `bot-briosos-web`; actual host, commit and consumer identity **BLOCKED: not inspected live** | Whatsapp-Bot feature branch above; root `/`; retain its **existing** launch command and queue binding, exact deployed command **BLOCKED**. No extra bot consumer. |
| Isolated pilot registry + CRM API | Service/host **BLOCKED: no isolated target supplied** | Whatsapp-Bot same branch; root `/crm`; config `crm/railway.pilot.toml`; `uvicorn crm_service.pilot_app:create_app --factory --host 0.0.0.0 --port ${PORT:-8080} --no-access-log` via the artifact's shell. |
| Isolated read projection | Service/host **BLOCKED: no isolated target supplied** | Same repo/branch/root `/crm`; config `crm/railway.pilot-worker.toml`; `python -m crm_service.pilot_sync`. Standby does no DB/source work. No RQ, Meta sends, follow-up or notification dispatcher. |
| Pilot frontend | Separate non-commercial preview/host **BLOCKED: none supplied** | chatty-bot-command-center same feature branch; root `/`; preserve its existing Cloudflare/Lovable build/runtime conventions. No production publication or new runtime/framework is prescribed. |
| Pilot database | Exact host/database **BLOCKED: no approved isolated DB supplied** | `CHATTY_PILOT_DATABASE_URL`, a dedicated logical PostgreSQL DB whose name starts `chatty_pilot_`; never the commercial `CRM_DATABASE_URL`. |

`crm-staging-api`, `crm-staging-sync`, `crm-api`, `crm-sync` and their databases
are **not** pilot targets. Their naming does not confer isolation. Do not repoint
the published commercial frontend's `CRM_BACKEND_URL`. Do not start a second
`run_railway`, `main:app`, embedded worker or RQ listener for the pilot.

The safe route is inside the **existing** signed `/webhook` and existing Briosos
consumer, after verified Phone Number ID + deployment checks. No app-wide Meta
webhook change is required or proposed. Until the real Briosos channel/consumer,
commit and isolated destinations are verified, activation remains **BLOCKED**.

## Settings by service (names only; configure privately later)

No values are changed by this delivery. Existing references are reused; do not
copy credentials into registry JSON or add blocks for Tanaka/Memo's/Velvet.

- Existing Briosos bot: `CHATTY_PILOT_BRIDGE_ENABLED=false` (absent also false),
  `CHATTY_PILOT_REGISTRY_URL` = approved isolated API HTTPS origin (**BLOCKED**).
  Reuse its current `BUSINESS_ID=briosos`, `WA_PHONE_NUMBER_ID`, `WA_APP_SECRET`,
  Meta/Supabase credentials, queue/Redis settings and existing
  `CRM_BOT_CONTROL_SECRET`. Its current human-control mode must already be
  `HUMAN_SUPPORT_PROVIDER=crm`, `CRM_CONTROL_ENABLED=true`; source export needs
  existing `CRM_SOURCE_READ_ENABLED` / `CRM_SOURCE_READ_SECRET`. Presence and
  deployed values of those requirements were **not** confirmed here.
- Isolated API: `CHATTY_PILOT_DATABASE_URL` (private DSN; target **BLOCKED**),
  `CHATTY_PILOT_COMMERCIAL_DATABASE_ID` (SHA-256 of the commercial CRM's canonical
  `host:port:database`, excluding user/password; **BLOCKED until target review**),
  `CHATTY_PILOT_STANDBY=true` (default),
  `CHATTY_PILOT_ACTIVATION_ENABLED=false` (default),
  `CHATTY_PILOT_EXPECTED_BOT_COMMIT` (exact reviewed 40-character deployed bot SHA;
  **BLOCKED / unknown**), `CRM_BUSINESSES=briosos`,
  `CRM_SOURCE_MODE=bot_api`,
  `CRM_USAGE_COLLECTION_ENABLED=false` until activation preparation,
  `NOTIFICATIONS_ENABLED=false`, `CRM_BOT_TEST_MODE=false`.
  Reuse existing `CRM_JWT_SECRET`, distinct `CRM_SYNC_SECRET`,
  `BRIOSOS_BOT_API_URL`, `BRIOSOS_BOT_CONTROL_SECRET`,
  `BRIOSOS_HUMAN_CONTROL_ENABLED` and `BRIOSOS_BOT_API_ENABLED` references.
  Do not inherit the commercial database URL. The isolation check also rejects
  the same canonical host/port/database if `CRM_DATABASE_URL` is present.
- Isolated read worker: same isolated DB/exclusion, `CRM_BUSINESSES=briosos`,
  `CHATTY_PILOT_STANDBY=true`, notifications disabled; existing worker settings and
  `BRIOSOS_BOT_API_URL`, `BRIOSOS_BOT_API_ENABLED`, `BRIOSOS_BOT_SOURCE_SECRET`
  reference the **same Briosos** read-export secret. No Supabase administrative
  key is needed in this worker or API. API has no need for the read-export secret.
- Separate pilot frontend host: existing private `PASSWORD_*`, existing
  `CRM_JWT_SECRET` matching the isolated API, existing `CRM_PILOT_OPERATOR_ID`
  and pilot/inbox flags for that host only; existing `CRM_BACKEND_URL` targets
  the isolated API (**BLOCKED / host unknown**). Keep existing per-account login,
  server-resolved actors and queue/alias claims. Never expose secrets with VITE_.

The DB exclusion fingerprint is an explicit review guard, not proof that two
network aliases cannot point to the same database. Confirm dedicated database
identity and both API/worker targets manually before migration. New businesses
can be prepared as drafts with channel IDs and authorized existing memberships;
only same-business connections already registered in server Settings can be
associated. Unregistered connections stay blocked, never borrow another brand's
credentials. No new per-business variable block is introduced by this pilot.

## Migrations and readiness — every file NOT APPLIED

**Only the approved isolated pilot PostgreSQL**, never any bot Supabase or shared
commercial CRM DB. No bot schema changes are needed in this turn.

Order for an empty isolated DB:

1. `crm/alembic/versions/0001_crm_independent_crm_schema.py` — NOT APPLIED.
2. `crm/alembic/versions/0002_sync_controls.py` — NOT APPLIED.
3. `crm/alembic/versions/0003_bot_commands.py` — NOT APPLIED.
4. `crm/alembic/versions/0004_delivery_receipts.py` — NOT APPLIED.
5. `crm/alembic/versions/0005_central_config_usage.py` — NOT APPLIED.
6. `crm/alembic/versions/0006_scoped_pilot.py` — NOT APPLIED.

After target review only, from `/crm`: `python -m crm_service.pilot_migrate`.
It uses an explicit Alembic configuration attribute, validates the isolated target,
and upgrades to `0006_scoped_pilot` without mutating process environment variables.
Existing isolated DBs advance incrementally, retaining their data. The pilot
factory's readiness requires exactly 0006. The ordinary factory remains compatible
with 0004/0005/0006; **the supplied active API commit predates this compatibility**.
Never let its existing `preDeployCommand = "alembic upgrade head"` upgrade a
commercial DB. The new pilot artifact has **no preDeploy migration**. Verify no
Railway dashboard override retains one before any proposed pilot deployment.

## Standby → explicit activation (not performed)

1. Resolve all BLOCKED targets/commits above, confirm feature branch has no live
   bindings, review code and obtain the isolated target. No resource creation is
   authorized in this turn. Apply only the isolated migration sequence manually
   after readiness compatibility review; start pilot API/read worker in standby.
   This cannot register Meta webhooks or consume bot queues/send messages.
2. Keep the existing Briosos ingress/queue/storage intact. Prepare the reviewed
   bot commit there with bridge **false**; do not change commercial services.
   Its authenticated GET `/internal/crm/v1/businesses/briosos/pilot-status` exposes
   actual deployment SHA, channel, storage fingerprint and collector counters
   without secrets or content. Unknown SHA/export compatibility blocks activation.
3. Open the separate pilot frontend with existing accounts. A standby GET of
   capabilities/businesses registers the same server-authenticated membership in
   the isolated crm_agents, as existing login does; it creates no new login.
   Admin selects Briosos, its verified channel ID, same-business bot/source
   connections and existing authorized operators/aliases. Save draft explicitly.
   Shared Tanaka/Velvet identities and Memo's queues are not edited.
4. **Publish version inactive** via Admin, POST `/v1/admin/pilot/publications`
   with `{draft_version:<saved numeric version>}`. This is a contract example,
   not a ready command with an invented target/version. Publication verifies the
   real Briosos status at runtime; freezes config/connections/operators; draft
   remains draft. It does not activate any channel or start synchronization.
5. For the reviewed test window only: privately set the existing Briosos bridge
   true and registry origin, exact expected bot SHA on isolated API; enable usage
   reception there; set isolated API/read worker standby false and API activation
   gate true. No test messages during this preparation interval: a bridge without
   active publication fails closed and does not try a legacy/alternate sender.
   Admin's **Activate only test number** sends the current revision and selected
   immutable version to `/v1/admin/pilot/activate`. Verify confirmed state;
   anything else remains blocked. No SQL/state manipulation substitutes this step.
6. The read worker loads only Briosos + 573105338124 through the existing
   authenticated GET/HEAD export, requiring its phone-filter handshake. Its normal
   bounded steps retain cursors/backoff and never mark a partial baseline complete.
   One manual bounded step from `/crm` is `python -m crm_service.pilot_sync --once`;
   it refuses standby/inactive state. Wait for the existing sync-status baseline
   completion before the five checks below. No historical notifications replay.

## Single authority, uncertainty and reversal

Only the existing Briosos consumer calls the existing commercial engine once,
under its current webhook claim and Redis/phone lock. A frozen business context
contains the verified channel, immutable published settings, opaque references,
existing operator/queue context and the **same legacy storage adapter**. No global
BUSINESS_ID, credential, env var or commercial setting is rewritten per request.
Other businesses/numbers retain the legacy path without registry/usage calls.
Unknown/mismatched configuration fails closed; no cross-brand/default fallback.

Human control still uses the authoritative bot RPC, confirmed pause, current owner
and expected version. Replayed commands use their existing immutable identity;
ambiguous sends query that same command and are never resent through another
sender. No queues/jobs/cursors are copied, replayed or reassigned during the switch;
pending follow-ups retain the original deployment/storage and existing fences.

To reverse: Admin selects the currently active publication and **Disable preserving
state** (`/v1/admin/pilot/disable`, current revision/version). Only after confirmation
set the existing Briosos bridge false and isolated workers/API back to standby.
The single existing consumer continues on the SAME up-to-date Supabase. In-flight
work keeps its captured authority and existing locks/fences; accepted/unknown
commands stay queryable. Do not revert to stale DB snapshots, replay a job, create
another sender, downgrade tables or delete pilot records. If registry is down,
manual bridge-off on that same single consumer is the recovery gate; preserve
jobs and query pending outcomes first, never re-enqueue an uncertain send.

The isolated API retains authenticated, assigned-operator GET control/command
recovery after disable/standby for the previously activated test conversation;
operational writes remain blocked. Do not shut down that API before resolving
outstanding command outcomes. The worker remains a read projection only.

Usage: each actual Gemini call is observed inside the existing retry mechanism;
transcription attempts are separate. Only an actual provider message ID records
reply acceptance, never delivery. Missing tokens/cost remain unknown. Authenticated
sink timeouts/failures are isolated in the existing bounded collector queue, with
accepted/failed/dropped/pending counters visible in Admin pilot health. No retry
or pricing engine is added. Collector memory can be lost at restart; accepted
central records are durable/idempotent. Counters never claim complete accounting.

## Five manual checks — own test number only; all PENDING

1. **Text:** +573105338124 → Briosos; one response, one claimed event, correct
   context/config version; another Briosos number and all commercial brands do
   not enter the pilot. Do not use customers to check this.
2. **Audio:** original and optional transcription retained; observe individual
   model attempts separately from accepted reply turns, without claiming delivery.
3. **Image:** existing media/history/caption works; one send/result, no duplicate
   or unapproved file/source access.
4. **Takeover/reply:** assigned existing operator takes control; wait for confirmed
   pause/owner/version, then send once. Query the same command after uncertainty;
   no simultaneous AI send, no alias/queue lockout, no automatic resend.
5. **Return to AI + reverse:** confirmed return, next test message handled by AI;
   disable publication and bridge without rolling back history/ownership/pending
   outcomes. Inspect stored accepted/unknown command identity before any retry.

**Activation is BLOCKED by unknown bot/worker deployment commits, unverified live
consumer/channel and missing approved isolated API/worker/frontend hosts/database.
These are required checks/configuration, not confirmed missing production values.**
