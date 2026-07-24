# Whatsapp-Bot

FastAPI WhatsApp sales bot with Chatwoot handoff and Gemini responses.

## Scalability setup

The app can still run without Redis for small deployments, but production scale should use the queue worker path.

### Required environment variables

Existing required variables are still needed:

- `SUPABASE_URL`
- `SUPABASE_KEY`
- `WA_VERIFY_TOKEN`
- `WA_TOKEN`
- `WA_PHONE_NUMBER_ID`
- `GEMINI_API_KEY`
- Chatwoot variables used by handoff: `CHATWOOT_BASE_URL`, `CHATWOOT_API_TOKEN`, `CHATWOOT_ACCOUNT_ID`, `CHATWOOT_INBOX_ID`

For scalable queued processing, also set:

- `REDIS_URL` - enables durable RQ queue processing.
- `QUEUE_NAME` - optional, defaults to `whatsapp-events`.
- `QUEUE_JOB_TIMEOUT_SECONDS` - optional, defaults to `180`.
- `GEMINI_MAX_CONCURRENT` - optional, defaults to `8` per process.
- `PHONE_LOCK_TTL_SECONDS` - optional, defaults to `180`.

### Deployment processes

Run one web process and one or more worker processes:

```bash
uvicorn main:app --host 0.0.0.0 --port ${PORT:-8080}
python -m workers.runner
```

The web process accepts webhooks quickly. When `REDIS_URL` is set, webhook payloads are placed on the queue and workers process Gemini, Meta, Supabase, and Chatwoot calls outside the request path.

### Database migration

Before enabling `REDIS_URL`, run `scalability.sql` in the Supabase SQL editor. It creates the webhook idempotency table and adds indexes for message history and Chatwoot conversation lookups.
