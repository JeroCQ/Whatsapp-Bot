# Coordinated central runtime activation

The existing bots remain the only Meta senders and retain their Redis queues.
`CHATTY_RUNTIME_URL` and a per-business `CHATTY_RUNTIME_TOKEN` opt an existing
deployment into authenticated startup registration. While the registry mode is
`legacy`, startup does not replace its storage or commercial configuration.
The central API enables its private router only with
`CHATTY_RUNTIME_REGISTRY_ENABLED=true`; tokens derive from its private
`CHATTY_RUNTIME_SECRET`. No credential belongs in a browser or this repository.

The registry stores the existing configured server connections, not new user
credentials. Public business/queue permissions continue to come from authenticated
CRM claims and membership. This registration alone is not a completed migration.

Final activation requires complete verified copies of all five datasets, copied
Storage bytes, compatible Briosos source metadata, idle/frozen existing workers
and HTTP operations, and a durable buffer for signed Meta ingress. Do not mark a
barrier verified before observing those facts. Never freeze an origin while a
required deployment approval is still pending.

After a successful cutover, every bot must report its expected business, Meta
channel, central storage origin and table prefix before its existing queue is
released. Use the normal CRM factory/worker for all businesses, not the
test-number-only pilot factories. Preserve customer frontend sign-ins and the
canonical Tanaka/Velvet and Memo's queue identities. Retain all original stores.

The dedicated `chatty_runtime` database login does not change the `postgres`
password. Its CRM schema has been provisioned by the existing Alembic chain.
The source/target copy and final deployment health checks are operational
requirements; no local application/test/build environment is required.
