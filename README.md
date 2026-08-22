# Whatsapp-Bot

FastAPI WhatsApp sales bot with Chatwoot handoff and Gemini responses.

## Deployments por negocio

El código es multi-despliegue, no multi-negocio: cada Railway debe definir un único
`BUSINESS_ID`, usar su propio Supabase/WhatsApp/Redis/Chatwoot inbox y cargar los
archivos permitidos desde `PRESAVED_FILES_JSON`. El backend administrativo rechaza
un `client_name` que no coincida con ese identificador, evitando cruces entre marcas.

Para crear Quesos Memo's sin modificar el bot live de Tanaka, sigue el checklist
completo de [`SETUP_MEMOS.md`](SETUP_MEMOS.md).

Para crear el despliegue aislado de Tanaka sobre Chatwoot self-hosted, Supabase,
Meta y Railway nuevos, sigue [`SETUP_TANAKA.md`](SETUP_TANAKA.md). El runbook
incluye el orden de creación, la lista exacta de variables y las pruebas de corte.

## Dashboard administrativo de Tanaka

The repository contains the initial dashboard-managed Tanaka system instruction at
`src/clients/tanaka/system_instruction.txt`. Real catalog PDFs are not committed to
the repository; the dashboard stores them in Supabase Storage and the bot resolves
`catalogo_pdf` to that Storage URL at runtime.

The password-only dashboard proxy now lives in Lovable/TanStack Start server routes,
not in this FastAPI service. Configure the proxy with server-only secrets (never
variables prefixed with `VITE_`):

- `DASHBOARD_BACKEND_URL=https://powerful-stillness-production-ffd8.up.railway.app`
- `DASHBOARD_API_KEY` with exactly the same value used by Railway
- `TANAKA_DASHBOARD_PASSWORD` with Tanaka's dashboard password
- `MEMOS_DASHBOARD_PASSWORD` with Memo's dashboard password

The Lovable server route sends `X-Dashboard-API-Key` to Railway, injects the
allowed `client_name`, and forwards browser traffic to `/api/current-si`,
`/api/generate-si-changes`, `/api/format-and-save-si`, `/api/si-history`, and
`/api/upload-catalog`. The backend stores GitHub metadata automatically from
Railway's native `RAILWAY_GIT_REPO_OWNER`, `RAILWAY_GIT_REPO_NAME`, and
`RAILWAY_GIT_BRANCH` variables, with manual `GITHUB_OWNER`, `GITHUB_REPO`, and
`GITHUB_BRANCH` used only as fallbacks outside Railway.

For dashboard Gemini calls, leave `GEMINI_DASHBOARD_MODEL` unset to use
`gemini-3.6-flash`, or set it to a concrete model returned by the Gemini
Developer API. Do not use old aliases such as `gemini-1.5-flash-latest`, and do
not rely on 2.5 model IDs for new API projects; the backend normalizes known old
IDs to `gemini-3.6-flash` and retries fallback models from
`GEMINI_DASHBOARD_FALLBACK_MODELS` before returning a provider error. If both
`GOOGLE_API_KEY` and `GEMINI_API_KEY` are set in Railway, remove
`GOOGLE_API_KEY` unless it is intentionally the same key, because the Google SDK
warns that it may prefer it. Full-system-instruction formatting can take longer
than proposal generation; use `DASHBOARD_FORMAT_TIMEOUT_SECONDS` (default `90`)
to control the save endpoint timeout separately from other dashboard calls.

Catalog PDFs and images are stored outside GitHub in Supabase Storage because real catalogs can
be tens of megabytes. Create a public Supabase Storage bucket named `catalogos`
(or set `CATALOG_STORAGE_BUCKET`) and let the dashboard upload to the fixed key
`{client_name}.{ext}`, for example `tanaka.pdf` or `memos.png`. Accepted formats are
PDF, JPEG, PNG, and WebP. Replacing the format removes the previous client object so
only one active catalog remains. The API uses Supabase resumable/TUS
uploads through the direct `*.storage.supabase.co` hostname so files larger than
the standard upload limit can succeed. The API returns the stable public URL from
`/api/upload-catalog` and `/api/current-catalog`; the bot also resolves
`catalogo_pdf` to the active deterministic URL and sends it as a WhatsApp document
or image according to its stored MIME type, so the old Railway `catalogo_tanaka`
link is no longer the source of truth. Keep `catalogo_tanaka` only to declare the
file id/description/caption for Gemini; its `link` field may remain as any valid
HTTPS placeholder because the bot overrides the `catalogo_pdf` link at runtime with
the deterministic Storage URL. `DASHBOARD_MAX_CATALOG_MB` defaults to `100`, and
files above that return `413`. On Supabase Free projects, also raise the Storage
file-size setting or upgrade as needed because Supabase can enforce a project-level
50 MB limit before the app limit is reached.

## Scalability setup

The app can still run without Redis for small deployments, but production scale should use the queue worker path.

### Required environment variables

Existing required variables are still needed:

- `SUPABASE_URL`
- `SUPABASE_SERVICE_ROLE_KEY` (recommended for the server-side database client;
  avoids RLS hiding `message_logs`). Existing deployments can continue using
  `SUPABASE_KEY` as a fallback, but it must contain the service-role secret, not
  the publishable/anon key.
- `WA_VERIFY_TOKEN`
- `WA_TOKEN`
- `WA_PHONE_NUMBER_ID`
- `GEMINI_API_KEY`
- Chatwoot variables used by handoff: `CHATWOOT_BASE_URL`, `CHATWOOT_API_TOKEN`, `CHATWOOT_ACCOUNT_ID`, `CHATWOOT_INBOX_ID`, and `CHATWOOT_ASSIGNMENT_MODE`. Set the mode to `automatic` to omit `assignee_id` and let Chatwoot's inbox collaborators, availability, and assignment policy control distribution. Set it to `fixed` only for an intentional rollback or single-agent deployment; fixed mode requires a positive numeric `CHATWOOT_ASSIGNEE_ID`.
- Chatwoot webhook/media security: `CHATWOOT_WEBHOOK_SECRET`, optional `CHATWOOT_MAX_ATTACHMENT_BYTES` (default 25 MiB)

### Isolated Chatwoot configuration

Each bot deployment must use its own Chatwoot account, inbox, agent API token, webhook secret, Supabase project, Redis instance, and `QUEUE_NAME`. `CHATWOOT_BASE_URL` may point to the shared installation, but it must be the HTTPS installation root (for example `https://app.chatwoot.com` or `https://chatwoot-production-example.up.railway.app`), with no `/api/v1`, `/app`, query, fragment, credentials, or trailing path.

Create an **account webhook** for only `message_created` and `conversation_status_changed`, targeting `https://BOT-DOMAIN/chatwoot-webhook`. Chatwoot v4.16.2 signs account webhooks with the webhook's generated secret using `X-Chatwoot-Timestamp` and `X-Chatwoot-Signature`; copy that secret into `CHATWOOT_WEBHOOK_SECRET`. The signature is `sha256=HMAC-SHA256(secret, "<timestamp>.<raw JSON body>")` and requests older than five minutes are rejected. Missing or invalid signatures return 401; account/inbox mismatches return 403. The Meta callback remains `https://BOT-DOMAIN/webhook`; do not route Meta through Chatwoot's native WhatsApp channel.

Never place webhook secrets or API tokens in URLs. Rotate a Chatwoot webhook secret by updating the Railway secret and the corresponding account webhook together during a controlled window. Provider logs contain only sanitized status/code/message fields. Mobile push notifications are configured on the Chatwoot installation, not in this bot.

`CHATWOOT_ASSIGNMENT_MODE` has no implicit default when Chatwoot is configured:

- `automatic`: conversation creation omits `assignee_id`. Add or remove agents in
  the Chatwoot inbox; the bot neither contains nor retrieves a collaborator list.
  A retained `CHATWOOT_ASSIGNEE_ID` is ignored and may be kept for rapid rollback.
- `fixed`: conversation creation includes the configured `CHATWOOT_ASSIGNEE_ID`.
  This bypasses automatic distribution and is rejected when that ID is missing.

Missing or unknown modes fail configuration validation rather than silently assigning
new customer conversations to an unexpected agent. Existing conversations are never
reassigned by changing this setting.

Chatwoot Cloud and a self-hosted Chatwoot are separate systems even when an
agent uses the same email address in both. For Memo's, the mobile client must be
logged into the custom server `https://chat.briosos.org`; a session connected to
`app.chatwoot.com` cannot receive its conversations or notifications. See the
[Memo's mobile-app runbook](SETUP_MEMOS.md#app-móvil-no-mezclar-chatwoot-cloud-con-el-self-hosted)
before changing bot or webhook settings.

For scalable queued processing, also set:

- `REDIS_URL` - enables durable RQ queue processing.
- `QUEUE_NAME` - optional, defaults to `whatsapp-events`. It **must be unique per bot/brand** when multiple bots share a Redis instance (for example, `whatsapp-events-memos`).
- `QUEUE_JOB_TIMEOUT_SECONDS` - optional, defaults to `180`.
- `GEMINI_MAX_CONCURRENT` - optional, defaults to `8` per process.
- `PHONE_LOCK_TTL_SECONDS` - optional, defaults to `180`.

### AI-controlled presaved files

Set `PRESAVED_FILES_JSON` to a JSON array. Each entry gives Gemini a safe ID and a
description that explains **when** it should send the file. The existing system prompt
can add more business rules using that ID. Files can use a permanent public HTTPS URL
(`link`) or a Meta media ID (`media_id`), but not both:

```json
[
  {
    "id": "catalogo_pdf",
    "description": "Catálogo de Tanaka Saludable; enviarlo cuando pidan el catálogo o quieran ver todos los productos.",
    "type": "document",
    "filename": "catalogo-tanaka.pdf",
    "caption": "Aquí tienes nuestro catálogo completo ☺️"
  }
]
```

The reserved `catalogo_pdf` entry does not need a `link`: the bot resolves the
current `catalogos/{BUSINESS_ID}.{ext}` object from Supabase Storage at send time.
Other entries must provide exactly one HTTPS `link` or Meta `media_id`.

Supported `type` values are `document`, `image`, `video`, and `audio`; other file
formats should use `document`. The model can select one or several configured IDs and
choose whether the files arrive before or after its text. Unknown IDs are ignored by
the application, so the model cannot send an unapproved file. Changes to this variable
take effect after restarting/redeploying the service.

No uses las variables históricas `catalogo_tanaka` o `catalogo_memos`: el runtime
solo carga `PRESAVED_FILES_JSON` y el despliegue se selecciona con `BUSINESS_ID`.

### Follow up automático

Gemini prepara un mensaje de seguimiento cuando la respuesta deja una venta pendiente.
La regla `FOLLOW UP POR FALTA DE RESPUESTA` del system prompt define el texto y
`follow_up_delay_minutes` (120 minutos por defecto), así que ambos se ajustan allí. Si
el cliente escribe antes, el mensaje pendiente se cancela; los handoffs tampoco generan
seguimiento.

El follow up usa trabajos programados de RQ: requiere `REDIS_URL` y un worker con
scheduler (el worker incluido ya usa `with_scheduler=True`). El token de cancelación
también vive en Redis, así que **no requiere modificar el esquema de Supabase**. Sin
Redis el bot responde normalmente, pero no programa seguimientos no durables.

Una respuesta de simple confirmación (`ok`, `listo`, `bueno`) no cierra el pendiente
si anteriormente se solicitaron datos. Gemini debe mencionar en el follow up los datos
concretos que todavía faltan. Además, cuando `RUN_WORKER_IN_WEB=false`, el launcher
verifica que exista al menos un worker RQ vivo; si no encuentra ninguno inicia un worker
embebido de respaldo para evitar que los jobs queden abandonados en Redis.

#### Prueba rápida sin esperar dos horas

En un ambiente de pruebas agrega temporalmente `FOLLOW_UP_TEST_DELAY_SECONDS=10` y
reinicia el servicio. Esta variable sustituye únicamente la espera del scheduler: el
mensaje continúa siendo generado con las reglas reales del system prompt.

1. Envía al bot una consulta que deje una pregunta de venta pendiente y no respondas.
2. Confirma en logs `FOLLOW UP] Programado ... en 10 segundos` y que el mensaje llegue
   aproximadamente diez segundos después.
3. Repite la consulta, pero responde antes de diez segundos. El job debe registrar
   `FOLLOW UP] Cancelado o reemplazado` y no debe enviar el recordatorio.
4. Elimina `FOLLOW_UP_TEST_DELAY_SECONDS` al terminar. Nunca debe quedar configurada en
   producción; sin ella se respetan los 120 minutos definidos por el system prompt.

La suite también verifica, sin dormir ni conectarse a Redis, que 120 minutos se
convierten en 7200 segundos y que el override reduce la espera a segundos.

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

### Topología recomendada para producción en Railway

Para una instalación pequeña se puede dejar un solo servicio con el worker embebido.
No hace falta crear `RUN_WORKER_IN_WEB`: su valor predeterminado es `true`. En esa
topología se puede eliminar cualquier servicio Railway adicional llamado `worker`, ya
que el proceso mostrado por `[LAUNCHER] ... starting embedded RQ worker subprocess`
es quien consume la cola.

Para escalar horizontalmente se recomienda conservar un servicio web y uno o más
servicios worker separados:

| Servicio | Start command | `QUEUE_NAME` | `RUN_WORKER_IN_WEB` |
| --- | --- | --- | --- |
| Web Memo's | el comando normal de `railway.json` | `whatsapp-events-memos` | `false` |
| Worker Memo's | `python -m workers.runner` | `whatsapp-events-memos` | no se necesita |

En Railway, `RUN_WORKER_IN_WEB=false` se agrega en **Variables** del servicio web; no
es una variable histórica ni provista automáticamente. El start command del worker se
configura como override en **Settings → Deploy → Custom Start Command**. Si Railway
aplica variables compartidas, se debe sobrescribir `QUEUE_NAME` en ambos servicios para
que coincida exactamente. El worker correcto no inicia Uvicorn: después de `Starting
Container` debe mostrar `[WORKER] Starting RQ worker`, `*** Listening on ...` y quedarse
escuchando. Un servicio que además muestra `Uvicorn running on ...` está ejecutando el
launcher web y no está configurado como worker dedicado.

Antes de desactivar el worker embebido, comprueba estas cuatro condiciones:

1. Web y worker imprimen el mismo `commit`.
2. Web y worker usan exactamente el mismo `QUEUE_NAME` específico de la marca.
3. El worker muestra `*** Listening on <QUEUE_NAME>...`.
4. La ruta `/` del web reporta `queue.workers_seen` mayor o igual a `1`.

La misma ruta también reporta `queue.web_queue_mode`. En la topología separada debe
ser `external_worker`; si dice `embedded_worker`, el servicio web todavía está lanzando
su propio consumidor. Este dato describe la configuración del web, mientras que
`workers_seen` confirma cuántos workers registra Redis.

Con esa topología se pueden añadir réplicas del servicio worker para absorber más
conversaciones sin duplicar el servidor web. La capacidad real depende sobre todo de
los límites de Gemini, Meta, Supabase y del valor de `GEMINI_MAX_CONCURRENT`; tener una
cola y varios workers permite escalar, pero no garantiza por sí solo una cifra fija de
conversaciones simultáneas.

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
