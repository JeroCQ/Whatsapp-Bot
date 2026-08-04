# Whatsapp-Bot

FastAPI WhatsApp sales bot with Chatwoot handoff and Gemini responses.

## Dashboard administrativo de Tanaka

The repository contains the initial dashboard-managed Tanaka files at:

- `src/clients/tanaka/system_instruction.txt`
- `public/catalogos/tanaka_catalogo.pdf`

The PDF committed to the repository is intentionally a valid placeholder. Replace it
from the dashboard with the real catalog before sharing it with customers. Railway
serves the committed catalog at
`https://powerful-stillness-production-ffd8.up.railway.app/public/catalogos/tanaka_catalogo.pdf`.
Set the existing Railway variable `catalogo_tanaka` to use that URL for the
`catalogo_pdf` entry.

The secure Lovable proxy is in `supabase/functions/dashboard-api/index.ts`, and its
admin allow-list migration is in
`supabase/migrations/20260804000000_dashboard_admins.sql`. Deploy both to the Supabase
project connected to Lovable, then configure these Edge Function secrets:

- `DASHBOARD_BACKEND_URL=https://powerful-stillness-production-ffd8.up.railway.app`
- `DASHBOARD_API_KEY` with exactly the same value used by Railway
- `DASHBOARD_FRONTEND_ORIGIN` with the published Lovable origin (no trailing slash)

After the migration, add an administrator from the Supabase SQL editor, replacing the
email with the exact email used to log in to Lovable:

```sql
insert into public.dashboard_admins (user_id)
select id from auth.users where email = 'ADMIN_EMAIL_HERE'
on conflict (user_id) do nothing;
```

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
- Chatwoot variables used by handoff: `CHATWOOT_BASE_URL`, `CHATWOOT_API_TOKEN`, `CHATWOOT_ACCOUNT_ID`, `CHATWOOT_INBOX_ID`

For scalable queued processing, also set:

- `REDIS_URL` - enables durable RQ queue processing.
- `QUEUE_NAME` - optional, defaults to `whatsapp-events`. It **must be unique per bot/brand** when multiple bots share a Redis instance (for example, `whatsapp-events-memos`).
- `QUEUE_JOB_TIMEOUT_SECONDS` - optional, defaults to `180`.
- `GEMINI_MAX_CONCURRENT` - optional, defaults to `8` per process.
- `PHONE_LOCK_TTL_SECONDS` - optional, defaults to `180`.

### AI-controlled presaved files

Set `catalogo_tanaka` to a JSON array. Each entry gives Gemini a safe ID and a
description that explains **when** it should send the file. The existing system prompt
can add more business rules using that ID. Files can use a permanent public HTTPS URL
(`link`) or a Meta media ID (`media_id`), but not both:

```json
[
  {
    "id": "catalogo_pdf",
    "description": "Catálogo de Tanaka Saludable; enviarlo cuando pidan el catálogo o quieran ver todos los productos.",
    "type": "document",
    "link": "https://example.com/catalogo.pdf",
    "filename": "catalogo-tanaka.pdf",
    "caption": "Aquí tienes nuestro catálogo completo ☺️"
  }
]
```

Supported `type` values are `document`, `image`, `video`, and `audio`; other file
formats should use `document`. The model can select one or several configured IDs and
choose whether the files arrive before or after its text. Unknown IDs are ignored by
the application, so the model cannot send an unapproved file. Changes to this variable
take effect after restarting/redeploying the service.

`catalogo_memos` y `PRESAVED_FILES_JSON` se conservan sin cambios para poder reutilizar
el código en otros negocios. Esta configuración de Tanaka solo carga `catalogo_tanaka`.

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
