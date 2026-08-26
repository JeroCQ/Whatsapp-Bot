# Desplegar un negocio nuevo: runbook completo y repetible

Este es el procedimiento canónico para desplegar otra marca sin tocar las que ya
funcionan. Sustituye en todo el documento:

- `<slug>`: identificador en minúsculas, sin espacios (por ejemplo `memos`).
- `<NEGOCIO>`: nombre comercial visible.
- `<RAILWAY_URL>`: dominio público del bot nuevo.

## Algoritmo que hay que memorizar

**Código → Supabase → Railway bootstrap → Redis → Meta → Chatwoot → Lovable → pruebas.**

La unidad de aislamiento es siempre:

> 1 negocio = 1 Railway + 1 Supabase + 1 Redis/cola + 1 número/Meta App +
> 1 cuenta e inbox Chatwoot + 1 perfil Lovable + secretos propios.

Nunca conviertas el Railway de una marca existente en otra ni copies referencias
de servicios de otro proyecto.

## 0. Preparar el código

1. Trabaja desde el mismo commit estable que usa el negocio de referencia.
2. Crea `src/clients/<slug>/system_instruction.txt` con la identidad, productos,
   precios, reglas de handoff y operación del negocio nuevo.
3. Confirma que `BUSINESS_ID=<slug>` coincide exactamente con ese directorio.
4. No agregues variables históricas `catalogo_<marca>`: los archivos se declaran
   únicamente con `PRESAVED_FILES_JSON`.

## 1. Supabase nuevo

1. Crea un proyecto vacío y guarda la contraseña de base de datos.
2. Ejecuta **una vez** todo `supabase/bootstrap.sql` en SQL Editor.
3. Confirma las tablas `customers`, `conversation_states`, `message_logs` y
   `processed_webhook_events`.
4. Confirma que Storage tenga el bucket público `catalogos`.
5. Copia Project URL a `SUPABASE_URL` y la clave secreta service-role a
   `SUPABASE_SERVICE_ROLE_KEY`. No uses anon/publishable y no expongas service-role
   en Lovable.
6. Si el proyecto se creó con un bootstrap anterior, ejecuta las migraciones de
   `supabase/migrations/` en orden.

El dashboard normaliza cualquier nombre local que suba el usuario a
`catalogos/<slug>.<ext>` y admite PDF, JPG/JPEG, PNG o WebP. No hay que pedirle al
usuario que renombre su archivo. Debe quedar un único formato activo.

## 2. Railway bootstrap (romper la dependencia circular de Meta)

Meta necesita una URL de webhook, pero Railway valida variables Meta al arrancar.
Despliega primero con placeholders **no funcionales**:

```env
BUSINESS_ID=<slug>
SUPABASE_URL=<url-real>
SUPABASE_SERVICE_ROLE_KEY=<service-role-real>
GEMINI_API_KEY=<clave-real>
WA_VERIFY_TOKEN=<secreto-aleatorio-definitivo>
WA_TOKEN=BOOTSTRAP_NOT_READY
WA_PHONE_NUMBER_ID=BOOTSTRAP_NOT_READY
PRESAVED_FILES_JSON=[{"id":"catalogo_pdf","description":"Catálogo de <NEGOCIO>; enviarlo cuando lo soliciten.","type":"document","filename":"Catálogo <NEGOCIO>.pdf","caption":"Aquí tienes nuestro catálogo."}]
```

Genera secretos con:

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

No envíes mensajes reales mientras existan los placeholders. Si Chatwoot aún no
está listo, omite **todas** las variables `CHATWOOT_*`; una configuración parcial
hace fallar la validación.

## 3. Redis y worker RQ

1. En el proyecto Railway nuevo usa **New → Database → Redis**. No uses una imagen
   Redis genérica ni agregues un Custom Start Command, `--dir` o módulos.
2. Espera `Ready to accept connections`. Un `FATAL CONFIG FILE ERROR` significa
   que el servicio fue personalizado incorrectamente: como aún está vacío, bórralo
   y crea otra base Redis oficial.
3. En las variables del **servicio web**, no en Redis, pulsa **Add a Variable
   Reference** y selecciona `Redis.REDIS_URL`.
4. Agrega:

```env
REDIS_URL=${{Redis.REDIS_URL}}
QUEUE_NAME=whatsapp-events-<slug>
RUN_WORKER_IN_WEB=true
```

Usa el selector visual porque el nombre real del servicio puede no ser `Redis`.
No pongas comillas ni copies solo `REDISHOST`. Railway no inyecta referencias de
forma condicional a la salud: el log `REDIS_URL not set` siempre significa que la
variable no llegó al servicio web.

Inicialmente usa un solo servicio web. `run_railway.py` inicia el worker embebido;
no crees otro worker ni uses `RUN_WORKER_IN_WEB=false`.

Antes de seguir, `/health` debe indicar `queue_enabled=true` y, tras unos segundos,
`queue.workers_seen>=1`. Los logs deben incluir `REDIS_URL detected` y
`Starting RQ worker for queue=whatsapp-events-<slug>`.

## 4. Meta/WhatsApp nuevo

1. Crea la Meta App/caso de uso WhatsApp y la WABA del negocio; agrega y verifica
   el número nuevo. Una empresa/app en desarrollo puede estar limitada a números
   de prueba hasta completar los requisitos de Meta.
2. Ya con `<RAILWAY_URL>`, configura:
   - Callback: `<RAILWAY_URL>/webhook`
   - Verify token: el mismo `WA_VERIFY_TOKEN` de Railway.
   - Suscripción: campo `messages`.
3. La verificación solo usa `WA_VERIFY_TOKEN`; no necesita un `WA_TOKEN` válido.
4. En API Setup copia **Phone number ID**, no WABA ID, App ID ni el número visible.
5. Usa un token temporal solo para pruebas. Para producción crea un system user,
   dale acceso a la app/WABA y permisos `whatsapp_business_messaging` y
   `whatsapp_business_management`, y genera un token permanente.
6. Reemplaza los placeholders y redepliega:

```env
WA_PHONE_NUMBER_ID=<phone-number-id-nuevo>
WA_TOKEN=<token-nuevo>
```

Nunca reutilices número, Phone Number ID, token o verify token de otra marca.

## 5. Chatwoot nuevo

### Recursos e identificadores

1. Puede compartirse la **instalación** (`CHATWOOT_BASE_URL`), pero crea una cuenta,
   inbox/API channel y agente exclusivos del negocio.
2. `CHATWOOT_API_TOKEN` es el **access token del agente/integración** con acceso a
   esa cuenta; no es un secreto de webhook ni token de inbox. No reutilices el
   agente/token de otra marca aunque técnicamente tenga acceso a ambas cuentas.
3. Crea en **Integrations → Webhooks** un **account webhook** hacia
   `<RAILWAY_URL>/chatwoot-webhook`, suscrito a `message_created` y
   `conversation_status_changed`.
4. `CHATWOOT_WEBHOOK_SECRET` es el secreto de ese webhook de Integrations. No uses
   el secreto/token mostrado dentro del inbox.
5. La raíz no lleva `/api/v1` ni `/app`.

```env
CHATWOOT_BASE_URL=https://chat.example.com
CHATWOOT_ACCOUNT_ID=<cuenta-nueva>
CHATWOOT_INBOX_ID=<inbox-nuevo>
CHATWOOT_API_TOKEN=<access-token-agente-nuevo>
CHATWOOT_WEBHOOK_SECRET=<secreto-account-webhook-nuevo>
CHATWOOT_MAX_ATTACHMENT_BYTES=26214400
```

### Asignación y notificaciones

Para varios agentes/round-robin o para replicar un inbox que ya funciona:

```env
CHATWOOT_ASSIGNMENT_MODE=automatic
```

En automático elimina `CHATWOOT_ASSIGNEE_ID`: el bot omite `assignee_id` y Chatwoot
aplica las reglas y notificaciones del inbox. Ser miembro permite ver/atender el
inbox, pero las notificaciones dependen de las preferencias y asignación de Chatwoot;
pruébalas en móvil.

Para forzar todas las conversaciones a una persona:

```env
CHATWOOT_ASSIGNMENT_MODE=fixed
CHATWOOT_ASSIGNEE_ID=<id-agente-nuevo>
```

El agente debe pertenecer a la cuenta e inbox. En automático, un assignee retenido
se ignora; omitir el modo mientras existe un assignee activa `fixed` por
compatibilidad. Configura siempre el modo explícitamente.

La app móvil debe iniciar sesión en el mismo servidor (Cloud o self-hosted) que
`CHATWOOT_BASE_URL`; tener el mismo correo no une instalaciones distintas.

## 6. Matriz completa de variables Railway

### Siempre nuevas por negocio

```env
BUSINESS_ID=<slug>
SUPABASE_URL=<nuevo>
SUPABASE_SERVICE_ROLE_KEY=<nuevo>
WA_PHONE_NUMBER_ID=<nuevo>
WA_TOKEN=<nuevo>
WA_VERIFY_TOKEN=<nuevo>
REDIS_URL=<referencia al Redis nuevo>
QUEUE_NAME=whatsapp-events-<slug>
DASHBOARD_API_KEY=<nuevo>
GITHUB_SI_PATH=src/clients/<slug>/system_instruction.txt
CHATWOOT_ACCOUNT_ID=<nuevo>
CHATWOOT_INBOX_ID=<nuevo>
CHATWOOT_API_TOKEN=<nuevo>
CHATWOOT_WEBHOOK_SECRET=<nuevo>
PRESAVED_FILES_JSON=<texto comercial nuevo>
```

`CHATWOOT_ASSIGNEE_ID` también es nuevo cuando el modo es `fixed`.

### Se pueden compartir conscientemente

- `GEMINI_API_KEY`: comparte cuota/facturación.
- `GITHUB_TOKEN`: solo si accede al mismo repo; puede aislarse con otro token.
- `CHATWOOT_BASE_URL`: solo si usan la misma instalación.
- Límites `DASHBOARD_*`, `CHATWOOT_MAX_ATTACHMENT_BYTES=26214400` y
  `CATALOG_STORAGE_BUCKET=catalogos` no son identidades.

`GITHUB_SI_PATH` puede omitirse porque se deriva de `BUSINESS_ID`, pero si se define
debe ser exactamente `src/clients/<slug>/system_instruction.txt`. `GITHUB_OWNER`,
`GITHUB_REPO` y `GITHUB_BRANCH` pueden omitirse cuando las variables automáticas de
Railway describen correctamente el repo/rama.

En Railway guarda `GITHUB_SI_PATH` sin comillas, espacios ni `/` inicial. El backend
normaliza comillas envolventes, CRLF y separadores `\\`, y registra al arrancar el
valor crudo con `repr()` para diagnosticar artefactos. Si hay un mismatch, revisa
también que el archivo exista con las mismas mayúsculas y extensión en la rama
`GITHUB_BRANCH`. Nunca apuntes una marca al prompt de otra.

### Eliminar variables históricas

`SUPABASE_KEY`, `catalogo_tanaka`, `catalogo_memos`, `GOOGLE_API_KEY`, variables
`KOMMO_*` y `PIN` no pertenecen a este runtime nuevo. Usa
`SUPABASE_SERVICE_ROLE_KEY`, `PRESAVED_FILES_JSON` y `GEMINI_API_KEY`. La contraseña
del dashboard vive server-side en Lovable, no en Railway.

## 7. Lovable: perfil, secretos y catálogo

Crea tres secretos **server-side**, nunca `VITE_*`:

```env
<SLUG>_DASHBOARD_BACKEND_URL=<RAILWAY_URL>
<SLUG>_DASHBOARD_API_KEY=<mismo DASHBOARD_API_KEY de Railway>
<SLUG>_DASHBOARD_PASSWORD=<contraseña nueva>
```

Usa este prompt, reemplazando `<slug>`, `<SLUG>` y `<NEGOCIO>`:

> Agrega el perfil `<slug>` (`<NEGOCIO>`) al dashboard sin cambiar los perfiles
> existentes. En el proxy server-side crea la configuración
> `<SLUG>_DASHBOARD_BACKEND_URL`, `<SLUG>_DASHBOARD_API_KEY` y
> `<SLUG>_DASHBOARD_PASSWORD`. Nunca expongas estos valores en variables `VITE_*`,
> respuestas al navegador o el bundle. Elige el backend solo desde la sesión
> autenticada y fuerza `client_name=<slug>` en servidor para `current-si`,
> `generate-si-changes`, `format-and-save-si`, `si-history`, `current-catalog` y
> `upload-catalog`; ignora o rechaza cualquier negocio enviado por el navegador.
> Para catálogos, acepta PDF/JPG/JPEG/PNG/WebP con cualquier nombre local y manda el
> archivo multipart al endpoint `/upload-catalog`; no subas directamente a Supabase
> ni renombres en el navegador. El backend normaliza a
> `catalogos/<slug>.<ext>` y elimina el formato anterior. Tras cargar, consulta
> `/current-catalog?client_name=<slug>` y muestra `filename`, `publicUrl` y tipo
> devueltos. Agrega pruebas que demuestren aislamiento de perfiles, rechazo de un
> `client_name` cruzado, normalización del nombre, reemplazo de extensión y ausencia
> de secretos en el bundle.

## 8. Pruebas antes de publicar el número

1. `/health`: status ok, cola habilitada y al menos un worker.
2. Verifica Meta con challenge y luego envía/recibe texto real.
3. Confirma registros solo en el Supabase nuevo.
4. Pregunta identidad/precios: nunca debe mencionar otra marca.
5. Sube desde Lovable un archivo con nombre arbitrario y confirma en Storage
   `catalogos/<slug>.<ext>`; cambia PDF por imagen y confirma que solo queda uno.
6. Pide catálogo por WhatsApp y abre el archivo recibido.
7. Prueba conversación sin handoff y otra que sí cumpla la regla comercial.
8. Confirma cuenta, inbox y asignación correctos en Chatwoot.
9. Responde texto/adjunto desde Chatwoot y resuelve; el bot debe reanudarse.
10. Con `FOLLOW_UP_TEST_DELAY_SECONDS=10`, prueba seguimiento y elimina la variable.
11. Repite un smoke test del negocio anterior para demostrar cero cruces.
12. Solo entonces publica el número. Si falla, desconecta/revierte únicamente el
    negocio nuevo.

Antes de subir el primer catálogo, `/api/current-catalog?client_name=<slug>` debe
responder 404 `catalog_not_found`; eso es un estado vacío normal, no un fallo. Un
502 incluye el status/mensaje real de Supabase más bucket y path: comprueba que
`SUPABASE_URL` y `SUPABASE_SERVICE_ROLE_KEY` pertenezcan al mismo proyecto y que el
bucket `catalogos` exista antes de reintentar.

Comandos útiles (sustituye placeholders):

```bash
curl -fsS '<RAILWAY_URL>/health'
curl -i --get '<RAILWAY_URL>/webhook' \
  --data-urlencode 'hub.mode=subscribe' \
  --data-urlencode 'hub.verify_token=<WA_VERIFY_TOKEN>' \
  --data-urlencode 'hub.challenge=123456'
curl -fsS -H 'api_access_token: <CHATWOOT_API_TOKEN>' \
  '<CHATWOOT_BASE_URL>/api/v1/profile'
```
