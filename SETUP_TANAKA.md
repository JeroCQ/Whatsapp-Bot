# Despliegue aislado de Tanaka en Railway y Chatwoot self-hosted

Este procedimiento crea un Railway nuevo para **Tanaka** sin reutilizar los datos,
colas, números ni recursos de otra marca. El repositorio es multi-despliegue: una
instancia atiende exactamente el negocio indicado por `BUSINESS_ID`.

## Decisiones que hay que confirmar antes del corte

Anota estas respuestas antes de cambiar webhooks. No bloquean la preparación de
Supabase, Chatwoot ni Railway, pero sí el paso a producción:

1. URL raíz pública de Chatwoot self-hosted (por ejemplo,
   `https://chat.briosos.org`).
2. Nombre del repositorio/rama que Railway desplegará y si el dashboard Lovable
   administrará también a Tanaka.
3. Si `tanaka@briosos.org` será el agente humano que recibe todos los handoffs.
4. Si se conservarán la clave de Gemini y el token de GitHub actuales. Se pueden
   compartir conscientemente; Supabase, Meta, Redis y Chatwoot no se comparten.

## Arquitectura objetivo

| Capa | Recurso exclusivo de Tanaka |
| --- | --- |
| Railway | Proyecto nuevo, un servicio web y un Redis nuevo |
| Supabase | Proyecto Tanaka pagado existente, actualizado sin borrar sus datos |
| Meta | WABA/número, Phone Number ID, token permanente y verify token nuevos |
| Chatwoot | Cuenta/workspace, API inbox, usuario agente, token y webhook propios |
| Aplicación | `BUSINESS_ID=tanaka`, prompt Tanaka y cola Tanaka |

La instalación/URL de Chatwoot sí puede ser compartida. Dentro de ella, la cuenta,
inbox, agente, token y webhook deben ser exclusivos. En Chatwoot, una **cuenta** es
el workspace/tenant; el usuario `tanaka@briosos.org` es un **agente** dentro de esa
cuenta. Crear solamente el correo no sustituye la cuenta ni el inbox.

## 1. Congelar y registrar el estado actual

1. No modifiques todavía ningún webhook de Meta ni la instancia de otra marca.
2. Registra en un gestor de secretos la URL, commit y variables del despliegue que
   se vaya a reemplazar. No pegues valores secretos en tickets, Git o este archivo.
3. Reserva dos URLs finales: `https://DOMINIO-TANAKA/webhook` para Meta y
   `https://DOMINIO-TANAKA/chatwoot-webhook` para Chatwoot.

## 2. Verificar y actualizar el Supabase Tanaka existente

No crees otro proyecto ni ejecutes `supabase/bootstrap.sql` sobre el proyecto
pagado. El esquema compartido demuestra que ya existen las cuatro tablas que usa el
runtime: `customers`, `conversation_states`, `message_logs` y
`processed_webhook_events`. Las tablas históricas `clients`, `products`,
`business_config` y `orders` son compatibles, pero este runtime no las consulta.
`dashboard_admins` pertenece a la capa administrativa y tampoco sustituye ninguna
de las cuatro tablas del bot.

El esquema **no está todavía completo para esta versión**: a
`conversation_states` le falta `follow_up_token`. El código actual lo usa para
programar, cancelar y consumir seguimientos. También faltan en el extracto los
índices actuales y no se puede confirmar desde un `CREATE TABLE` si existe el bucket
Storage `catalogos`, sus políticas o el estado de RLS.

1. Haz un backup o snapshot y confirma que las variables apuntan al proyecto Tanaka
   pagado, no al de otra marca.
2. Abre `supabase/upgrade_existing_tanaka.sql`. Ejecuta primero únicamente la consulta
   de IDs Chatwoot duplicados de la cabecera. Debe devolver **cero filas**. Si devuelve
   datos, conserva la fila que corresponda al ticket activo y limpia los IDs obsoletos
   antes de continuar.
3. Ejecuta el archivo completo en **SQL Editor**. Es idempotente: agrega solo
   `follow_up_token`, crea los índices que faltan y crea/corrige el bucket público
   `catalogos`; no elimina tablas ni filas históricas.
4. Verifica que la última consulta del archivo devuelva `true` en las siete columnas.
5. En **Project Settings → API**, copia la URL de este mismo proyecto a
   `SUPABASE_URL` y su clave secreta **service_role** a
   `SUPABASE_SERVICE_ROLE_KEY`. No uses anon/publishable y no expongas service-role
   en Lovable ni en el navegador.
6. Si el dashboard usa autenticación Supabase, verifica aparte que
   `dashboard_admins` tenga RLS habilitado, ningún policy para navegador y los grants
   de `anon`/`authenticated` revocados, como define
   `supabase/migrations/20260804000000_dashboard_admins.sql`. El extracto del esquema
   no contiene metadatos suficientes para confirmar esos controles.

## 3. Preparar Meta Cloud API

1. Agrega y verifica el número nuevo en la configuración de WhatsApp de Tanaka.
2. Copia su **Phone number ID** a `WA_PHONE_NUMBER_ID`.
3. Crea un token permanente de system user con los permisos de WhatsApp necesarios
   y guárdalo como `WA_TOKEN`; no uses el token temporal de prueba.
4. Genera un secreto independiente con
   `python -c "import secrets; print(secrets.token_urlsafe(32))"` y guárdalo como
   `WA_VERIFY_TOKEN`.
5. No configures el callback todavía: primero debe pasar el health check Railway.

## 4. Crear Tanaka dentro de Chatwoot self-hosted

El correo aún no creado es un prerrequisito para `CHATWOOT_ASSIGNEE_ID`, pero no
requiere un cambio de código.

1. Desde Super Admin crea una cuenta/workspace **Tanaka** separada.
2. Invita o crea `tanaka@briosos.org` dentro de esa cuenta y asígnalo como agente.
   El usuario debe aceptar/activar la invitación antes de la prueba móvil.
3. Dentro de la cuenta Tanaka crea un **API inbox** exclusivo. No uses el canal
   nativo de WhatsApp de Chatwoot: Meta entrega mensajes a este bot.
4. Registra los IDs numéricos de cuenta e inbox como `CHATWOOT_ACCOUNT_ID` y
   `CHATWOOT_INBOX_ID`.
5. Desde el perfil del agente/integración exclusivo genera el access token que será
   `CHATWOOT_API_TOKEN`. Dale acceso a la cuenta Tanaka.
6. Obtén el ID numérico del usuario `tanaka@briosos.org` y úsalo como
   `CHATWOOT_ASSIGNEE_ID`. No escribas el correo en esa variable.
7. Cuando Railway tenga dominio, crea un **account webhook** hacia
   `https://DOMINIO-TANAKA/chatwoot-webhook`, suscrito únicamente a
   `message_created` y `conversation_status_changed`. Copia el secreto generado a
   `CHATWOOT_WEBHOOK_SECRET`.
8. Usa en `CHATWOOT_BASE_URL` solo la raíz HTTPS self-hosted, sin `/app`,
   `/api/v1`, query ni path adicional.

## 5. Crear Railway en el orden seguro

1. Crea **New Project → Deploy from GitHub repo** y selecciona este repositorio.
   No clones un servicio que pueda arrastrar secretos de otra marca.
2. Agrega un servicio Redis nuevo dentro del proyecto Tanaka.
3. Mantén inicialmente un único servicio web. `railway.json` ejecuta
   `run_railway.py`, que inicia también el worker embebido cuando existe Redis.
4. Carga las variables de la sección siguiente y despliega.
5. Genera un dominio público para el servicio web.
6. Abre `/` y comprueba `status: "ok"`, `queue.enabled: true` y
   `queue.workers_seen >= 1`. En logs confirma la cola
   `whatsapp-events-tanaka`.
7. Solo entonces registra el callback Meta `https://DOMINIO-TANAKA/webhook` con
   el mismo `WA_VERIFY_TOKEN` y suscribe `messages`.
8. Crea/activa el webhook Chatwoot descrito en el paso anterior.

## 6. Qué hacer exactamente con las variables actuales

### Las 11 variables mostradas

| Variable | Acción | Valor de Tanaka |
| --- | --- | --- |
| `QUEUE_NAME` | **Cambiar** | `whatsapp-events-tanaka` |
| `REDIS_URL` | **Cambiar** | Referencia al Redis **nuevo**, normalmente `${{Redis.REDIS_URL}}` |
| `RUN_WORKER_IN_WEB` | **Eliminar** (recomendado) | Sin definir equivale a worker embebido; alternativamente `true`. Nunca `false` en esta topología inicial |
| `SUPABASE_SERVICE_ROLE_KEY` | **Cambiar respecto de otra marca / verificar para Tanaka** | `service_role` secreto del Supabase Tanaka pagado existente |
| `SUPABASE_URL` | **Cambiar respecto de otra marca / verificar para Tanaka** | URL del Supabase Tanaka pagado existente |
| `WA_PHONE_NUMBER_ID` | **Cambiar** | Phone Number ID del número nuevo |
| `WA_TOKEN` | **Cambiar** | Token permanente nuevo autorizado para ese número |
| `WA_VERIFY_TOKEN` | **Cambiar** | Secreto aleatorio nuevo, idéntico al configurado en Meta |
| `GITHUB_SI_PATH` | **Fijar/verificar** | `src/clients/tanaka/system_instruction.txt` |
| `GITHUB_TOKEN` | **Conservar o cambiar** | Conservar solo si tiene el permiso mínimo de contenido para este repo; uno separado aísla permisos |
| `CHATWOOT_ASSIGNEE_ID` | **Cambiar después de crear el agente** | ID numérico de `tanaka@briosos.org`, no correo, nombre ni inbox ID |

Por tanto, cambian obligatoriamente `QUEUE_NAME`, `REDIS_URL`, las tres variables
de WhatsApp y `CHATWOOT_ASSIGNEE_ID`. Las dos variables Supabase deben apuntar al
proyecto Tanaka pagado existente: cámbialas solo si los valores actuales pertenecen
a otro proyecto. `RUN_WORKER_IN_WEB` se elimina o queda `true`;
`GITHUB_SI_PATH` se verifica y `GITHUB_TOKEN` puede conservarse conscientemente.

### Variables que faltan en la lista y deben agregarse

| Variable | Requerida | Valor/origen |
| --- | --- | --- |
| `BUSINESS_ID` | Sí | `tanaka` exactamente, en minúsculas |
| `GEMINI_API_KEY` | Sí | Clave válida de Gemini; separada si se quiere aislar cuota/facturación |
| `CHATWOOT_BASE_URL` | Para handoff | Raíz HTTPS del Chatwoot self-hosted |
| `CHATWOOT_ACCOUNT_ID` | Para handoff | ID numérico de la cuenta Tanaka |
| `CHATWOOT_INBOX_ID` | Para handoff | ID numérico del API inbox Tanaka |
| `CHATWOOT_API_TOKEN` | Para handoff | Token del agente/integración Tanaka |
| `CHATWOOT_WEBHOOK_SECRET` | Para handoff | Secreto del account webhook Tanaka |
| `PRESAVED_FILES_JSON` | Para catálogo | JSON mostrado debajo |
| `CATALOG_STORAGE_BUCKET` | Recomendable | `catalogos` (es también el default) |
| `DASHBOARD_API_KEY` | Si se usa dashboard | Secreto nuevo compartido solo con el proxy server-side de Tanaka |
| `DASHBOARD_CORS_ORIGINS` | Si hay acceso web directo | Origen HTTPS exacto; omitir si todo pasa por proxy server-side |

Valor de `PRESAVED_FILES_JSON`. No incluyas `link`: `catalogo_pdf` es el catálogo
administrado por el dashboard y el backend descubre en runtime el objeto vigente
`catalogos/tanaka.{ext}` en Supabase:

```json
[{"id":"catalogo_pdf","description":"Catálogo de Tanaka Saludable; enviarlo cuando pidan el catálogo, precios generales o quieran ver todos los productos.","type":"document","filename":"Catalogo_Tanaka.pdf","caption":"Aquí tienes nuestro catálogo completo ☺️"}]
```

`PRESAVED_FILES_JSON` define para Gemini el ID permitido, cuándo usarlo, el nombre y
el caption; no es el almacenamiento del archivo. El PDF real vive únicamente en
Supabase Storage y se reemplaza desde el dashboard. Un `link` sigue siendo obligatorio
para otros archivos preguardados que no tengan el ID reservado `catalogo_pdf` (salvo
que usen `media_id`).

No agregues `SUPABASE_KEY`, `GOOGLE_API_KEY`, `catalogo_tanaka`,
`catalogo_memos` ni `PIN`. Son nombres históricos, ambiguos o pertenecen al proxy,
no a este servicio. Tampoco copies referencias Railway que nombren el proyecto o
Redis de otra marca.

## 7. Dashboard, catálogo y webhooks

1. En Lovable configura como secretos **server-only**
   `TANAKA_DASHBOARD_BACKEND_URL`, `TANAKA_DASHBOARD_API_KEY` y
   `TANAKA_DASHBOARD_PASSWORD`. La API key debe coincidir con
   `DASHBOARD_API_KEY` del Railway Tanaka y nunca ser `VITE_*`.
   `MEMOS_DASHBOARD_API_KEY` es exclusivamente la credencial del perfil Memo's:
   debe coincidir con el Railway Memo's, nunca con Tanaka, y no debe ser un valor
   débil como `123456`.
2. No copies `PRESAVED_FILES_JSON` a Lovable; pertenece a las variables del Railway
   Tanaka. Tampoco copies `DASHBOARD_CORS_ORIGINS` a Lovable. Si Lovable llama al
   backend mediante su proxy server-side, elimina `DASHBOARD_CORS_ORIGINS` de
   Railway o déjalo vacío. Solo configúralo con el origen HTTPS exacto de Lovable
   si el navegador llama directamente a FastAPI.
3. Sube el catálogo desde el perfil Tanaka y confirma que Supabase contiene un solo
   objeto vigente `catalogos/tanaka.{pdf|jpg|jpeg|png|webp}`.
4. Meta apunta a `/webhook`. Chatwoot apunta a `/chatwoot-webhook`. No intercambies
   estas URLs y no pongas secretos en ellas.

### Texto exacto para Lovable

Copia este encargo y sustituye únicamente los valores secretos en su gestor
server-side:

> Configura el perfil Tanaka para usar exclusivamente un proxy server-side. Crea
> `TANAKA_DASHBOARD_BACKEND_URL` con la URL pública del Railway Tanaka,
> `TANAKA_DASHBOARD_API_KEY` con el mismo secreto aleatorio fuerte configurado como
> `DASHBOARD_API_KEY` en Railway Tanaka, y `TANAKA_DASHBOARD_PASSWORD` con una
> contraseña fuerte independiente. Nunca expongas estos valores como `VITE_*`, en
> el bundle, en respuestas al navegador ni en logs. El proxy debe fijar
> `client_name=tanaka` en servidor y enviar `X-Dashboard-API-Key`; no debe aceptar
> del navegador otro backend ni otro `client_name`. Conserva
> `MEMOS_DASHBOARD_API_KEY` únicamente para el Railway Memo's y reemplaza cualquier
> valor débil como `123456` por un secreto aleatorio. No agregues
> `PRESAVED_FILES_JSON` ni `DASHBOARD_CORS_ORIGINS` a Lovable: son configuración del
> Railway. Como las llamadas pasan por el proxy server-side, no llames FastAPI
> directamente desde el navegador.

Genera las dos API keys por separado, por ejemplo con
`python -c "import secrets; print(secrets.token_urlsafe(32))"`. El valor Tanaka se
copia tanto a `TANAKA_DASHBOARD_API_KEY` en Lovable como a `DASHBOARD_API_KEY` en
Railway Tanaka. El valor Memo's se copia tanto a `MEMOS_DASHBOARD_API_KEY` en
Lovable como a `DASHBOARD_API_KEY` en Railway Memo's. Nunca cruces ambos valores.

## 8. Pruebas de aceptación antes de anunciar el número

Ejecuta en este orden y detente ante el primer fallo:

1. `/` devuelve estado correcto, cola habilitada y al menos un worker.
2. Los logs muestran `BUSINESS_ID=tanaka`, el commit esperado y la cola
   `whatsapp-events-tanaka`, sin imprimir secretos.
3. Meta acepta la verificación GET del webhook.
4. Un mensaje nuevo recibe una respuesta propia del prompt Tanaka.
5. “Envíame el catálogo” entrega el archivo Tanaka desde el Supabase Tanaka existente.
6. Un mensaje que requiere asesor crea la conversación únicamente en la cuenta e
   inbox Tanaka y queda asignada a `tanaka@briosos.org`.
7. Una respuesta pública desde Chatwoot llega al WhatsApp correcto; resolver la
   conversación reanuda el bot.
8. Una firma Chatwoot inválida devuelve 401 y un account/inbox cruzado devuelve 403.
9. Prueba temporalmente `FOLLOW_UP_TEST_DELAY_SECONDS=10`, confirma envío y
   cancelación, y elimina la variable inmediatamente después.
10. Inicia sesión en la app con **Custom server** apuntando a la raíz self-hosted,
    no a `app.chatwoot.com`; habilita las notificaciones del inbox Tanaka.

## Rollback

Si falla el corte, desconecta solo el callback del número Tanaka o restaura sus
valores registrados. No borres recursos ni revoques tokens antiguos durante la
ventana de rollback y no modifiques el proyecto de otra marca.

## Diagnóstico: Railway reinicia con `catalogo_pdf must define exactly one...`

Ese error identifica una versión anterior del código, no un fallo de Redis,
Supabase ni Lovable. En la versión actual, `catalogo_pdf` puede omitir `link` y
`media_id`; el catálogo se resuelve desde Supabase. Compara siempre el hash que
imprime `[WORKER] ... commit=<hash>` con el commit que contiene este runbook.

Cuando ocurre:

1. El worker alcanza a iniciar porque `workers.runner` no importa `bot.py`.
2. Uvicorn importa `main.py`, que importa `bot.py` y valida
   `PRESAVED_FILES_JSON`.
3. La versión antigua rechaza el catálogo sin link, el proceso web termina y el
   launcher apaga correctamente el worker y scheduler hijos.
4. Lovable recibe 502 porque Railway no tiene un proceso HTTP vivo. Cambiar CORS,
   contraseñas o el proxy no corrige ese 502.

### Solución correcta

Despliega en Railway un commit que incluya la excepción de catálogo administrado
en `file_catalog.py` y confirma en logs que el hash cambió. Mantén después este JSON
sin URL duplicada:

```json
[{"id":"catalogo_pdf","description":"Catálogo de Tanaka Saludable; enviarlo cuando pidan el catálogo, precios generales o quieran ver todos los productos.","type":"document","filename":"Catalogo_Tanaka.pdf","caption":"Aquí tienes nuestro catálogo completo ☺️"}]
```

No consideres terminado el redeploy hasta que `/` responda 200 y Lovable deje de
mostrar `backend 502`.

### Recuperación temporal si aún no puedes desplegar el commit nuevo

Restaura provisionalmente un `link` HTTPS en `PRESAVED_FILES_JSON`. Esto permite
que la versión antigua arranque, aunque el código nuevo seguirá sustituyéndolo por
el catálogo vigente de Supabase al enviarlo. Elimina otra vez el link después de
desplegar y verificar la versión actual. Esta recuperación es preferible a cambiar
Redis, Supabase, Chatwoot o CORS, que no participan en el error de importación.
