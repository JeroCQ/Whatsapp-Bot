# Alta canónica de un negocio nuevo

Este runbook aplica **solo a un proyecto nuevo**. Para actualizar una marca que ya
tiene datos, no ejecutes el bootstrap: usa
[`UPGRADE_EXISTING_BRANDS.md`](UPGRADE_EXISTING_BRANDS.md) y
`supabase/upgrade_existing_brand.sql`. El alta limpia usa
`supabase/bootstrap.sql`; ambos caminos deben terminar con las mismas capacidades,
pero nunca deben compartir datos ni credenciales.

## Principio de aislamiento

Este repositorio es multi-despliegue, no multi-tenant. Elige un identificador
estable, en minúsculas y sin espacios (por ejemplo `acme`) y fija
`BUSINESS_ID=acme`. Una instancia atiende exactamente ese negocio. Crea para ella:

* proyecto y rama/despliegue Railway propios;
* proyecto Supabase y `service_role` propios;
* número, Phone Number ID, token y verify token de Meta propios;
* Redis limpio y `QUEUE_NAME` propios;
* cuenta, API inbox, token y secretos Chatwoot propios (la instalación raíz sí
  puede ser compartida conscientemente);
* secretos del dashboard propios.

No clones variables de Tanaka, Memo's o Velvet. Gemini y GitHub solo pueden
compartirse por una decisión explícita sobre cuota y acceso; nunca reutilices IDs,
colas, números, bases, inboxes ni secretos. Antes de abrir el número, verifica que
una conversación de prueba solo aparece en Supabase, Redis y Chatwoot de la marca.

## Orden obligatorio: GitHub → Supabase → Meta → Chatwoot → Railway → dashboard

### 1. GitHub

1. Crea `src/clients/<business_id>/system_instruction.txt` en una rama real ya
   publicada en GitHub. No inventes `main` si el trabajo está en otra rama.
2. Revisa que esa rama contenga `railway.json`, `supabase/bootstrap.sql` y la
   instrucción de la marca. Railway y el dashboard deben apuntar al commit/rama
   que realmente existe.
3. Crea un token GitHub de mínimo privilegio únicamente si el dashboard editará
   la instrucción. Anota owner, repositorio, rama y ruta; no pongas el token en el
   frontend.

### 2. Supabase

1. Crea un proyecto vacío exclusivo y ejecuta **una vez** el contenido completo de
   `supabase/bootstrap.sql` en SQL Editor. Este es el camino para un proyecto
   nuevo; no ejecutes un upgrade ni una reparación parcial después del bootstrap.
2. Confirma las comprobaciones finales, tablas, restricciones, índices, roles y
   bucket `catalogos`. Guarda **Project URL** y la clave secreta `service_role`.
3. La variable `SUPABASE_URL` es la raíz `https://<project-ref>.supabase.co`,
   **sin `/rest/v1`**. `SUPABASE_SERVICE_ROLE_KEY` es solo servidor: nunca una
   publishable/anon key y nunca una variable del navegador.

### 3. Meta

1. Prepara la app/business y un número de WhatsApp exclusivo. Registra su Phone
   Number ID y crea un token permanente de system user.
2. Genera un `WA_VERIFY_TOKEN` aleatorio exclusivo. Aún no conectes el callback.
3. Después de validar Railway, configura
   `https://<dominio-del-bot>/webhook`, usa el mismo verify token y suscribe
   `messages`. No apuntes el número al bot de otra marca.

### 4. Chatwoot

1. Crea una cuenta/workspace, colaboradores y un **API inbox** exclusivos. Meta
   entrega al bot; no uses un inbox nativo de WhatsApp para este flujo.
2. Usa `CHATWOOT_ASSIGNMENT_MODE=automatic` y **omite**
   `CHATWOOT_ASSIGNEE_ID`. Así Chatwoot aplica colaboradores, disponibilidad y su
   política de asignación. `fixed` y un assignee numérico se reservan para un
   rollback intencional o una instalación de un solo agente.
3. Usa como `CHATWOOT_BASE_URL` únicamente la raíz HTTPS, sin `/app`, `/api/v1` ni
   otra ruta. Crea un token de agente/integración con acceso a esa cuenta.
4. En el API inbox fija `https://<dominio-del-bot>/chatwoot-webhook`. Copia el
   campo **`secret`** del canal a `CHATWOOT_API_INBOX_WEBHOOK_SECRET`. No copies
   **`hmac_token`**: sirve para identidad del contacto y no firma este webhook.
   Un administrador puede confirmar ambos campos mediante
   `GET /api/v1/accounts/{account_id}/inboxes/{inbox_id}`.
5. Un **account webhook** es opcional. Si se crea, apunta al mismo callback y usa
   un secreto distinto en `CHATWOOT_WEBHOOK_SECRET`; no sustituye el `secret` del
   API inbox. Nunca incluyas secretos en la URL.

### 5. Railway

1. Crea un proyecto nuevo desde el repositorio y selecciona **la rama real** del
   paso 1. No clones un servicio de otra marca. Conserva `railway.json`: contiene
   el build/start y la política de reinicio canónicos; no reemplaces el start
   command del servicio web.
2. Agrega un **Redis oficial limpio** dentro de este proyecto. No cambies su start
   command, deja **un solo volumen** y no agregues RedisBloom ni plugins. Despliega
   Redis y espera a que esté sano **antes** de desplegar el web.
3. En Variables del web usa **Add Reference**, selecciona el servicio por su nombre
   vigente y referencia `REDIS_URL` (la expresión suele verse como
   `${{Redis.REDIS_URL}}`, pero el nombre puede ser distinto). No escribas una URL
   antigua. Si eliminas o recreas Redis, la referencia queda huérfana: elimínala y
   créala otra vez contra el servicio nuevo.
4. Carga las variables de las tablas siguientes. Deja `RUN_WORKER_IN_WEB` **sin
   definir**: el launcher iniciará el worker embebido. No crees un segundo servicio
   worker en la topología inicial.
5. Despliega y comprueba dominio, commit, `BUSINESS_ID`, `QUEUE_NAME`, conexión a
   Redis y el log `[LAUNCHER] REDIS_URL detected; starting embedded RQ worker
   subprocess.`

Si Railway muestra **“Waiting for dependencies”**, no cambies el código ni el
start command: abre el grafo del proyecto, identifica la referencia rota, confirma
que Redis tiene un deployment sano y que su nombre coincide con Add Reference.
Elimina/recrea `REDIS_URL` si Redis fue sustituido, despliega Redis primero y luego
redeploy del web. Revisa también que no haya volúmenes duplicados o un Redis
personalizado esperando RedisBloom.

#### Prompt estándar para Railway Agent

Pega este texto sin añadir instrucciones de migración o cambios de código:

```text
Configura este proyecto Railway para un negocio WhatsApp aislado. Usa la rama
real actualmente seleccionada y conserva railway.json. Crea un único Redis oficial
limpio, con su start command predeterminado, un solo volumen y sin RedisBloom.
Despliega Redis antes del servicio web. En el web crea REDIS_URL con Add Reference
al REDIS_URL del nombre vigente del servicio Redis; si existe una referencia rota,
elimínala y recréala. No cambies el start command del web, no crees un worker
separado y deja RUN_WORKER_IN_WEB sin definir para usar el worker embebido. No
copies variables, secretos, IDs, volúmenes ni recursos de ninguna otra marca. Si
aparece “Waiting for dependencies”, diagnostica primero el deployment de Redis y
la referencia REDIS_URL; no modifiques código, SQL ni railway.json.
```

### 6. Dashboard

Configura el proxy **server-side** después de conocer el dominio Railway. Fija en
servidor el `client_name` igual a `BUSINESS_ID`, apunta su backend al dominio de
esta marca y envía `X-Dashboard-API-Key`. La API key, password, GitHub token y
`service_role` nunca deben usar prefijo `VITE_` ni llegar al navegador. Prueba
lectura/edición de la instrucción, historial y carga del catálogo al objeto
`catalogos/<business_id>.<ext>` sin alterar otra marca.

## Inventario de variables: fuente y obligatoriedad

### Núcleo y proveedores

| Variable | Obligación para un alta | Fuente / valor |
| --- | --- | --- |
| `BUSINESS_ID` | **Obligatoria** | Identificador elegido, minúsculas; coincide con instrucción, dashboard y catálogo. |
| `SUPABASE_URL` | **Obligatoria** | Supabase → Project URL raíz, sin `/rest/v1`. |
| `SUPABASE_SERVICE_ROLE_KEY` | **Obligatoria** | Supabase → clave secreta `service_role`; solo Railway. |
| `WA_VERIFY_TOKEN` | **Obligatoria** | Secreto generado por el operador y repetido en Meta callback. |
| `WA_TOKEN` | **Obligatoria** | Token permanente del system user Meta de la marca. |
| `WA_PHONE_NUMBER_ID` | **Obligatoria** | Meta WhatsApp → Phone Number ID de la marca. |
| `GEMINI_API_KEY` | **Obligatoria** | Google AI Studio/proyecto Gemini autorizado. |
| `REDIS_URL` | **Obligatoria en producción** | Railway Add Reference al Redis exclusivo vigente. |
| `QUEUE_NAME` | **Obligatoria para aislamiento** | `whatsapp-events-<business_id>`, exclusiva aunque Redis también lo sea. |
| `PRESAVED_FILES_JSON` | **Obligatoria si el bot envía archivos** | Array JSON de IDs/descripciones/tipo/nombre/caption autorizados para esta marca; `[]` si ninguno. |
| `CATALOG_STORAGE_BUCKET` | Opcional | `catalogos` por defecto; bucket creado por bootstrap. |
| `APP_ENV` | Opcional | `production` por defecto. |
| `GEMINI_MAX_CONCURRENT` | Opcional | Entero; default `8`. |
| `PHONE_LOCK_TTL_SECONDS` | Opcional | Segundos; default `180`. |
| `GEMINI_OUTAGE_RECOVERY_SINCE` | Solo reparación controlada | Timestamp de inicio de una caída; normalmente omitida y retirada tras recuperar. |

### Chatwoot

| Variable | Obligación para un alta con handoff | Fuente / valor |
| --- | --- | --- |
| `CHATWOOT_BASE_URL` | **Obligatoria** | Raíz HTTPS de la instalación. |
| `CHATWOOT_ACCOUNT_ID` | **Obligatoria** | ID numérico de la cuenta exclusiva. |
| `CHATWOOT_INBOX_ID` | **Obligatoria** | ID numérico del API inbox exclusivo. |
| `CHATWOOT_API_TOKEN` | **Obligatoria** | Token del agente/integración con acceso a la cuenta. |
| `CHATWOOT_ASSIGNMENT_MODE` | **Obligatoria** | `automatic`. |
| `CHATWOOT_ASSIGNEE_ID` | **Omitir** | Solo es obligatorio si deliberadamente se usa `fixed`. |
| `CHATWOOT_API_INBOX_WEBHOOK_SECRET` | **Obligatoria para respuestas de agentes** | Campo `secret` del API inbox, no `hmac_token`. |
| `CHATWOOT_WEBHOOK_SECRET` | Opcional | Secreto independiente del account webhook opcional. |
| `CHATWOOT_MAX_ATTACHMENT_BYTES` | Opcional | Entero; default `26214400` (25 MiB). |

Si no habrá Chatwoot, omite el bloque completo; una configuración parcial impide
arrancar. Para el flujo de producción esperado por este repositorio, configura el
bloque completo anterior.

### Dashboard, GitHub y ajustes opcionales

| Variable | Obligación | Fuente / valor |
| --- | --- | --- |
| `DASHBOARD_API_KEY` | **Obligatoria si hay dashboard** | Secreto fuerte exclusivo, igual en Railway y proxy server-side. |
| `DASHBOARD_CORS_ORIGINS` | Según frontend directo | Orígenes exactos separados por coma; el proxy server-side es preferible. |
| `GITHUB_TOKEN` | **Obligatoria para guardar instrucciones** | Token GitHub de mínimo privilegio. |
| `GITHUB_OWNER`, `GITHUB_REPO`, `GITHUB_BRANCH` | Normalmente automáticas | Railway aporta `RAILWAY_GIT_REPO_OWNER`, `RAILWAY_GIT_REPO_NAME` y `RAILWAY_GIT_BRANCH`; fija las canónicas solo fuera de Railway o para una rama distinta. |
| `GITHUB_SI_PATH` | Opcional | `src/clients/<business_id>/system_instruction.txt`; omitida usa la ruta derivada. |
| `GEMINI_DASHBOARD_MODEL`, `GEMINI_DASHBOARD_FALLBACK_MODELS` | Opcionales | Modelo concreto y lista separada por comas; omitir usa defaults del runtime. |
| `DASHBOARD_REQUESTS_PER_MINUTE` | Opcional | Default `30`. |
| `DASHBOARD_MAX_TEXT_CHARS` | Opcional | Default `100000`. |
| `DASHBOARD_MAX_PDF_BYTES` | Opcional | Default `104857600`. |
| `DASHBOARD_MAX_CATALOG_MB` | Opcional | Default `100`. |
| `DASHBOARD_EXTERNAL_TIMEOUT_SECONDS` | Opcional | Default `30`. |
| `GITHUB_TIMEOUT_SECONDS` | Opcional | Default `10`. |
| `DASHBOARD_FORMAT_TIMEOUT_SECONDS` | Opcional | Default `90`. |
| `DASHBOARD_STORAGE_TIMEOUT_SECONDS` | Opcional | Default `300`. |
| `DASHBOARD_HISTORY_MAX_PAGE_SIZE` | Opcional | Default `50`. |

El proxy externo añade, con nombres propios de su plataforma,
`<BRAND>_DASHBOARD_BACKEND_URL`, `<BRAND>_DASHBOARD_API_KEY` y
`<BRAND>_DASHBOARD_PASSWORD`; son secretos server-side, no variables del bot.

### Variables retiradas o que no deben crearse

* `SUPABASE_KEY`: compatibilidad de despliegues antiguos; un alta usa
  `SUPABASE_SERVICE_ROLE_KEY`.
* `GOOGLE_API_KEY`: retírala para evitar precedencia ambigua; usa
  `GEMINI_API_KEY`.
* `CHATWOOT_API_URL` y `CHATWOOT_ACCESS_TOKEN`: aliases heredados; usa
  `CHATWOOT_BASE_URL` y `CHATWOOT_API_TOKEN`.
* `RUN_WORKER_IN_WEB`: déjala sin definir en la topología canónica. No la fijes a
  `false` sin un worker dedicado ya verificado, y no hace falta fijarla a `true`.
* URLs antiguas como `catalogo_tanaka` y referencias Redis copiadas: no son fuente
  del catálogo ni deben existir en una marca nueva.
* No inventes `REDIS_HOST`, `REDIS_PORT`, variables RedisBloom ni un start command
  como variable; la única conexión canónica es `REDIS_URL` por referencia.

## Validación extremo a extremo (un 200 no basta)

1. `GET /` confirma estado, cola habilitada y worker; los logs muestran marca,
   cola y commit correctos.
2. Valida el challenge de Meta y envía un mensaje real. **`POST /webhook 200` solo
   confirma el ACK rápido**: no demuestra que el mensaje haya sido procesado.
3. Verifica por separado: log de **enqueue** en la cola correcta; consumo del job
   por el **worker embebido**; llamada/respuesta de **Gemini**; entrega de respuesta
   por **Meta**; y métrica `[METRIC] whatsapp_message_processed`.
4. Comprueba persistencia en el Supabase de esta marca, no en el de otra. Prueba
   duplicado/reintento, catálogo, pedido y comprobante.
5. Fuerza un handoff: debe crearse en el account/inbox correcto, asignarse según
   `automatic`, aceptar una respuesta pública del agente sin “Failed to send” y
   volver a IA al resolver. Confirma las firmas del API inbox.
6. Desde el dashboard prueba instrucción, historial y catálogo. Después ejecuta
   una prueba negativa con otro `client_name`: debe rechazarse y no leer ni escribir
   datos ajenos.

## Confusiones observadas durante Velvet (y cómo evitarlas)

* Se seleccionó mentalmente una rama deseada en vez de comprobar la rama real:
  confirma rama y commit en GitHub **y** en el deployment Railway.
* Se trató Redis como código de la aplicación: usa el servicio oficial limpio, su
  start predeterminado, un volumen y ningún RedisBloom.
* Una referencia sobrevivió visualmente después de borrar Redis, pero apuntaba a
  un recurso inexistente: Add Reference se debe recrear con el nombre vigente.
* Se desplegó web antes de Redis y “Waiting for dependencies” pareció un fallo del
  bot: sana Redis primero y luego redeploy web.
* Se pegó el endpoint REST completo en `SUPABASE_URL`: debe ser la raíz sin
  `/rest/v1`.
* Se intentó corregir infraestructura cambiando start commands o quitando
  `railway.json`: ambos deben conservarse; el worker ya viene embebido.
* Se interpretó `POST /webhook 200` como respuesta completa: solo es ACK; faltaba
  comprobar enqueue, worker, Gemini/Meta y `whatsapp_message_processed`.
* Se confundieron `secret` y `hmac_token` de Chatwoot y el account webhook con el
  callback del API inbox: son credenciales y funciones diferentes.
* Se copió configuración de otra marca para avanzar rápido: un alta no está lista
  hasta que número, Supabase, Redis/cola, account/inbox y secretos sean propios.


## Catálogos múltiples (proyectos nuevos y existentes)

`bootstrap.sql` (nuevo) y `upgrade_existing_brand.sql` (existente) crean `catalog_assets`. En Lovable, crea un ID técnico estable `catalogo_<slug>` y un nombre público por pieza, sube cada archivo por separado y referencia el ID exacto desde `requested_files` del system instruction. No agregues una variable Railway por catálogo: solo `BUSINESS_ID`, credenciales del Supabase aislado y `CATALOG_STORAGE_BUCKET`. Verifica creación, edición de nombre, reemplazo, eliminación y que otro `BUSINESS_ID` reciba HTTP 422.
