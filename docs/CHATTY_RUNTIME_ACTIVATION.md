# Coordinated central runtime activation

The existing bots remain the only Meta senders and retain their Redis queues.
`CHATTY_RUNTIME_URL` and a per-business `CHATTY_RUNTIME_TOKEN` opt an existing
deployment into authenticated startup registration. While the registry mode is
`legacy`, startup does not replace its storage or commercial configuration.
The central API enables its private router only with
`CHATTY_RUNTIME_REGISTRY_ENABLED=true`; connection-specific enrollment tokens are
kept in the RLS-protected server registry. Existing master-derived tokens remain
compatible. No credential belongs in a browser or this repository.

The registry stores the existing configured server connections, not new user
credentials. Public business/queue permissions continue to come from authenticated
CRM claims and membership. This registration alone is not a completed migration.

Final activation requires complete verified copies of all five datasets, copied
Storage bytes, compatible Briosos source metadata, idle/frozen existing workers
and HTTP operations, and a durable buffer for signed Meta ingress. Do not mark a
barrier verified before observing those facts. Never freeze an origin while a
required deployment approval is still pending.

November 20 exception approved by Daniel: Briosos can start empty. Its registry
`migration.strategy=fresh` and `fresh_start_authorized=true` explicitly select
the new schema in `supabase/central/briosos.sql`; neither SQL nor private Storage
access to its separate organization is needed. Preparation hashes the actual
empty destinations instead of pretending to copy that source. Its old CRM
operational rows are not reinstalled into the fresh tenant. All original stores
and both private CRM snapshots remain intact; business drafts, accounts, usage
and support records are retained. Overlapping operator memberships are unioned
while the commercial preferences and queue identity remain authoritative.

Memo's and Velvet copy logins are read-only, have no RLS bypass and use explicit
SELECT policies on only the ten bot tables and their own catalog/media keys.
No existing password is reset. No managed central Storage owner is changed.

After a successful cutover, every bot must report its expected business, Meta
channel, central storage origin and table prefix before its existing queue is
released. Use the normal CRM factory/worker for all businesses, not the
test-number-only pilot factories. Preserve customer frontend sign-ins and the
canonical Tanaka/Velvet and Memo's queue identities. Retain all original stores.

The dedicated `chatty_runtime` database login does not change the `postgres`
password. Its CRM schema has been provisioned by the existing Alembic chain.
The source/target copy and final deployment health checks are operational
requirements; no local application/test/build environment is required.

The private owner factory is `crm_service.central_app:create_app`; it preserves
the pilot until the registry actually publishes the central mode. Its sync
counterpart is `python -m crm_service.central_worker`. Neither wrapper activates
a migration merely by starting. API source-copy requests run read-only source
transactions using that API's own configured database connection; the private
target connection is delivered server-to-server by the registry. Login/JWT keys
are never replaced. Source workers acknowledge drain before the barrier can be
verified. The private migration-operation endpoint serializes actual copies,
checks row/Storage integrity, and will not install without all five final
snapshots and observed drain evidence. Its status output contains only counts.

Managed Storage permissions and owners are not changed in the central project.
The existing Storage API connection checks only the particular destination
buckets needed by the file copy. Table ownership applies only to the explicitly
listed new prefixed destinations and migration metadata; RLS remains enabled.
The repeatable, narrowly scoped ownership step is
`supabase/central/runtime_permissions.sql`. It must run before activation and
does not change the owners or policies of the original Tanaka tables.
