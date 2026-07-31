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
- `QUEUE_NAME` - optional, defaults to `whatsapp-events`. It **must be unique per bot/brand** when multiple bots share a Redis instance (for example, `whatsapp-events-memos`).
- `QUEUE_JOB_TIMEOUT_SECONDS` - optional, defaults to `180`.
- `GEMINI_MAX_CONCURRENT` - optional, defaults to `8` per process.
- `PHONE_LOCK_TTL_SECONDS` - optional, defaults to `180`.

### AI-controlled presaved files

Set `catalogo_memos` to a JSON array. Each entry gives Gemini a safe ID and a
description that explains **when** it should send the file. The existing system prompt
can add more business rules using that ID. Files can use a permanent public HTTPS URL
(`link`) or a Meta media ID (`media_id`), but not both:

```json
[
  {
    "id": "catalogo_memos_pdf",
    "description": "Catálogo de Quesos Memo's; enviarlo cuando pidan el catálogo o quieran ver todos los productos.",
    "type": "document",
    "link": "https://example.com/catalogo.pdf",
    "filename": "catalogo-memos.pdf",
    "caption": "Aquí tiene nuestro catálogo completo, patrón 🧀"
  }
]
```

Supported `type` values are `document`, `image`, `video`, and `audio`; other file
formats should use `document`. The model can select one or several configured IDs and
choose whether the files arrive before or after its text. Unknown IDs are ignored by
the application, so the model cannot send an unapproved file. Changes to this variable
take effect after restarting/redeploying the service.

`PRESAVED_FILES_JSON` se conserva sin cambios para que el mismo entorno de Railway
pueda seguir usándose con el otro proyecto; este bot solo carga `catalogo_memos`.

If Railway logs show `Queue enabled: True`, the web service can see `REDIS_URL`. By default, the Railway launcher also starts an embedded worker so queued messages cannot remain unanswered. Use either that embedded worker **or** one verified dedicated worker, never both. Set `RUN_WORKER_IN_WEB=false` only after the dedicated service logs `[WORKER] Starting RQ worker` with the expected queue and commit.

### Railway deployment checklist

1. Add a Redis service/plugin in Railway.
2. In the bot web service variables, set `REDIS_URL` from that Redis service.
3. Deploy the bot web service normally. Railway uses `railway.json`, whose start command runs `python run_railway.py` and starts its embedded worker.
4. Check the same service logs for both lines:

```text
[LAUNCHER] REDIS_URL detected; starting embedded RQ worker subprocess.
[WORKER] Starting RQ worker for queue=whatsapp-events commit=<same-commit-as-web>
```

5. Open the web app root URL (`https://your-app.up.railway.app/`) and check the JSON. `queue.enabled` should be `true`, and `queue.workers_seen` should be at least `1` after the worker is running.

For a separate worker service, override Railway's start command with `python -m workers.runner`, copy the same variables and commit, and verify its logs contain `[WORKER] Starting RQ worker`. Only then set `RUN_WORKER_IN_WEB=false` on the web service. Logs containing only Uvicorn startup (`Uvicorn running on ...`) mean the supposed worker is actually another web process and will not consume queued jobs.

Never leave a dedicated worker running while also setting `RUN_WORKER_IN_WEB=true`. RQ assigns each job to whichever worker wins the race; if the two Railway services run different commits or brand variables, customers can receive answers from the wrong bot. Likewise, bots sharing Redis must use different `QUEUE_NAME` values.

### Local/development commands

Run the Railway-style web process, which only enqueues when `REDIS_URL` exists:

```bash
python run_railway.py
```

Run only the FastAPI web process:

```bash
uvicorn main:app --host 0.0.0.0 --port ${PORT:-8080}
```

Run one worker process:

```bash
python -m workers.runner
```

The Procfile also defines both process types:

```Procfile
web: python -m py_compile main.py chatwoot_api.py config.py database.py queue_client.py processing_lock.py workers/runner.py run_railway.py && python run_railway.py
worker: python -m workers.runner
```

The web process accepts webhooks quickly. When `REDIS_URL` is set, webhook payloads are placed on the queue and workers process Gemini, Meta, Supabase, and Chatwoot calls outside the request path.

### Database migration

Before enabling `REDIS_URL`, run `scalability.sql` in the Supabase SQL editor. It creates the webhook idempotency table and adds indexes for message history and Chatwoot conversation lookups.

### Where to see logs and latency

Railway shows app logs per service:

- Web service logs show webhook receipt, queue status, Meta send metrics, and fallback queue errors.
- Worker service logs show Gemini processing, media forwarding, Chatwoot handling, and end-to-end message duration.

Search Railway logs for these markers:

- `[QUEUE ERROR]` - Redis/RQ enqueue failed and the web process fell back to a FastAPI background task.
- `[METRIC] gemini_message_logic` - Gemini text response latency.
- `[METRIC] gemini_audio_transcription` - Gemini audio transcription latency.
- `[METRIC] http_request host=graph.facebook.com` - WhatsApp/Meta API request latency/status.
- `[METRIC] http_request host=<your-chatwoot-host>` - Chatwoot API request latency/status.
- `[METRIC] whatsapp_message_processed` - full WhatsApp message processing time.
- `[METRIC] chatwoot_event_processed` - full Chatwoot webhook processing time.

You can also open the root endpoint in a browser. It returns queue diagnostics without exposing secrets, including queued jobs, failed jobs, and how many RQ workers Redis can currently see.
