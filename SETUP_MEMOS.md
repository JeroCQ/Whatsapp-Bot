# Puesta en producción de Quesos Memo's sin tocar Tanaka

> **Actualización futura del Memo's existente:** antes de desplegar una versión
> nueva de este repositorio, crea un backup de su Supabase y ejecuta
> `supabase/upgrade_existing_brand.sql`. La consulta inicial de IDs Chatwoot
> duplicados debe devolver cero filas y la verificación final debe devolver `true`
> en todo. No ejecutes `supabase/bootstrap.sql`, conserva `BUSINESS_ID=memos` y no
> reemplaces sus secretos, Redis, número Meta, catálogo o Chatwoot por los de Tanaka
> o Velvet. Sigue el corte completo de `docs/UPGRADE_EXISTING_BRANDS.md`.

## Arquitectura final

No conviertas el Railway de Tanaka en Memo's. Déjalo intacto y crea un segundo proyecto Railway desde este mismo repositorio. Cada despliegue ejecuta una sola marca mediante `BUSINESS_ID`; el backend rechaza cualquier `client_name` distinto al de ese despliegue. Cada marca tiene su propio WhatsApp, Supabase, Redis, inbox de Chatwoot, secretos y URL. Lovable es el único frontend compartido y actúa como enrutador de perfiles.

## 0. Antes de empezar

1. Anota la URL pública, variables y commit actualmente desplegado en Tanaka.
2. No elimines ni cambies variables, webhooks o servicios del proyecto Tanaka.
3. Genera secretos nuevos para Memo's. Nunca copies `WA_TOKEN`, `WA_VERIFY_TOKEN`, `DASHBOARD_API_KEY`, `PIN`, Redis ni claves Supabase de Tanaka.
4. Puedes reutilizar una clave Gemini y un token GitHub si conscientemente quieres compartir sus cuotas/permisos; para aislamiento total, crea otros.

## 1. Supabase nuevo

1. En Supabase crea una organización/proyecto para Memo's, elige región cercana y guarda la contraseña de base de datos.
2. Abre **SQL Editor**, pega todo `supabase/bootstrap.sql` y ejecútalo una vez.
   **No ejecutes `supabase/upgrade_existing_tanaka.sql`: ese archivo solo actualiza
   el Supabase histórico de Tanaka y no es el inicializador de proyectos nuevos.**
   `bootstrap.sql` ya incluye las mismas funciones agregadas después a Tanaka:
   idempotencia/cola, vínculo Chatwoot, cancelación de follow-ups, memoria de datos
   del cliente y pedido, catálogo y allow-list administrativa protegida.
3. Confirma que la consulta final devuelva `true` en todas sus columnas y que en
   **Storage** exista el bucket público `catalogos`.
4. En **Project Settings → API** copia:
   - Project URL → `SUPABASE_URL`.
   - La clave secreta `service_role` → `SUPABASE_SERVICE_ROLE_KEY`. No uses la `anon`/publishable key y nunca la expongas en Lovable.
5. No copies datos o tablas de Tanaka: el proyecto nuevo debe comenzar vacío.

## 2. WhatsApp/Meta nuevo

1. En Meta Business configura o selecciona la cuenta de WhatsApp de Memo's y agrega/verifica su número nuevo.
2. En WhatsApp → API Setup copia **Phone number ID** → `WA_PHONE_NUMBER_ID`.
3. Crea un token permanente de system user con permisos de WhatsApp para producción → `WA_TOKEN`. El token temporal sirve solo para una prueba corta.
4. Genera localmente un texto aleatorio largo → `WA_VERIFY_TOKEN`, por ejemplo `python -c "import secrets; print(secrets.token_urlsafe(32))"`.
5. Espera a tener la URL Railway antes de configurar el webhook. Después usa `https://DOMINIO-MEMOS/webhook` como callback y el mismo `WA_VERIFY_TOKEN`; suscribe el campo `messages`.
6. Envía y recibe un mensaje de prueba. No reutilices el Phone Number ID ni el token de Tanaka.

## 3. Chatwoot

1. En la instalación de Chatwoot crea una **cuenta separada** y un inbox/API channel exclusivo para Memo's. No uses la cuenta, inbox ni agentes de Tanaka.
2. Copia el ID numérico de la cuenta → `CHATWOOT_ACCOUNT_ID` y el ID numérico del inbox nuevo → `CHATWOOT_INBOX_ID`.
3. Desde el perfil de un agente/integración exclusivo de Memo's copia el access token → `CHATWOOT_API_TOKEN`.
4. Usa la raíz de la instalación, sin `/api/v1` al final, como `CHATWOOT_BASE_URL`.
5. En el **API inbox** configura `https://DOMINIO-MEMOS/chatwoot-webhook` como
   webhook URL y copia el `secret` firmante del canal a
   `CHATWOOT_API_INBOX_WEBHOOK_SECRET`; no uses el HMAC token de identidad. Este es
   el callback que entrega respuestas de agentes y evita “Failed to send”. Un
   account webhook adicional es opcional y usa un secreto distinto en
   `CHATWOOT_WEBHOOK_SECRET`.

## 4. Railway nuevo

1. Crea **New Project → Deploy from GitHub repo** y selecciona este repositorio y rama. No clones el servicio de Tanaka si Railway arrastrará secretos compartidos.
2. Agrega Redis dentro del proyecto Memo's.
3. Usa un solo servicio web inicialmente. `railway.json` ejecuta web + worker embebido; no crees un worker separado todavía.
4. Carga las variables de la tabla siguiente. En Railway usa referencias del proyecto Memo's, por ejemplo `${{Redis.REDIS_URL}}`; no dejes referencias `${{powerful-stillness.*}}`, pues apuntan al proyecto anterior.
5. Despliega, abre `/` y exige: `status=ok`, `queue_enabled=true`, `queue.workers_seen>=1`.
6. En logs confirma `[WORKER] Starting RQ worker for queue=whatsapp-events-memos`.
7. Configura los webhooks de Meta y Chatwoot solo después de que el health check pase.

### Variables: qué eliminar, conservar o cambiar

| Variable anterior | Acción en Memo's | Valor/origen |
|---|---|---|
| `catalogo_memos` | **Eliminar** | Reemplazada por `PRESAVED_FILES_JSON`. |
| `catalogo_tanaka` | **Eliminar** | Ya no hay variables por marca en el código. |
| `GOOGLE_API_KEY` | **Eliminar** | El código usa `GEMINI_API_KEY`; evita precedencia ambigua del SDK. |
| `KOMMO_PRIVATE_TOKEN`, `KOMMO_BASE_URL`, `KOMMO_REQUEST_TIMEOUT_SECONDS` | **Eliminar** | No existe integración Kommo en este código. |
| `PIN` | **Eliminar de Railway** | El PIN/contraseña pertenece al proxy server-side de Lovable, no al bot. |
| `BUSINESS_ID` | **Agregar/cambiar** | Exactamente `memos`, en minúsculas. |
| `SUPABASE_URL` | **Cambiar** | URL del Supabase nuevo. |
| `SUPABASE_KEY` | **Eliminar** | Usa el nombre explícito `SUPABASE_SERVICE_ROLE_KEY`. |
| `SUPABASE_SERVICE_ROLE_KEY` | **Agregar/cambiar** | Secreto service-role del Supabase nuevo. |
| `WA_PHONE_NUMBER_ID`, `WA_TOKEN` | **Cambiar** | Número y token nuevos de Meta. |
| `WA_VERIFY_TOKEN` | **Cambiar** | Secreto aleatorio nuevo; debe coincidir con Meta. |
| `CHATWOOT_INBOX_ID` | **Cambiar** | Inbox exclusivo de Memo's. |
| `CHATWOOT_ACCOUNT_ID` | **Cambiar** | Cuenta exclusiva de Memo's. Nunca compartir la cuenta de Tanaka. |
| `CHATWOOT_API_TOKEN` | **Cambiar** | Token del agente/integración exclusivo de Memo's. |
| `CHATWOOT_ASSIGNEE_ID` | **Agregar** | ID numérico del agente `memos@briosos.org`; fuerza la asignación al crear cada handoff y evita depender de la política Default. |
| `CHATWOOT_WEBHOOK_SECRET` | **Opcional/cambiar** | Secreto de un account webhook Memo's separado, si se conserva. |
| `CHATWOOT_API_INBOX_WEBHOOK_SECRET` | **Agregar/cambiar** | `secret` firmante del API inbox Memo's; requerido para entregar respuestas del asesor. |
| `CHATWOOT_MAX_ATTACHMENT_BYTES` | **Agregar opcional** | Máximo de descarga; default `26214400` (25 MiB). |
| `CHATWOOT_BASE_URL` | **Cambiar** | Raíz HTTPS de Chatwoot self-hosted, sin `/api/v1` ni `/app`. |
| `GEMINI_API_KEY` | **Conservar o cambiar** | Puede compartirse; una clave/proyecto separado aísla cuota y facturación. |
| `GEMINI_DASHBOARD_MODEL` | **Conservar** | Puede omitirse para usar el default del código. |
| `GITHUB_TOKEN` | **Conservar o cambiar** | Token con permiso de contenido sobre este repo; queda solo en Railway. |
| `CATALOG_STORAGE_BUCKET` | **Conservar** | `catalogos`. |
| `PRESAVED_FILES_JSON` | **Cambiar** | Usa el JSON exacto mostrado abajo. |
| `REDIS_URL` | **Cambiar** | Referencia al Redis del proyecto Memo's: `${{Redis.REDIS_URL}}`. |
| `QUEUE_NAME` | **Cambiar** | `whatsapp-events-memos`; nunca compartir nombre con Tanaka. |
| `DASHBOARD_API_KEY` | **Cambiar** | Secreto aleatorio propio de Memo's; mismo valor en Railway y secreto server-side de Lovable. |
| `DASHBOARD_CORS_ORIGINS` | **Conservar/cambiar** | Origen HTTPS exacto de Lovable, sin path; aunque el proxy server-side es preferible. |
| `DASHBOARD_EXTERNAL_TIMEOUT_SECONDS` | **Conservar** | El valor actual si ya funciona; default `30`. |
| `DASHBOARD_HISTORY_MAX_PAGE_SIZE` | **Conservar** | Default `50`. |
| `DASHBOARD_MAX_CATALOG_MB` | **Conservar** | Default `100`; debe ser compatible con el límite de Storage. |
| `DASHBOARD_MAX_PDF_BYTES` | **Eliminar** | No se usa actualmente; el límite efectivo es `DASHBOARD_MAX_CATALOG_MB`. |
| `DASHBOARD_MAX_TEXT_CHARS` | **Conservar** | Default `100000`. |
| `DASHBOARD_REQUESTS_PER_MINUTE` | **Conservar** | Default `30`. |
| `DASHBOARD_STORAGE_TIMEOUT_SECONDS` | **Conservar** | Default `300`. |

También puedes agregar `DASHBOARD_FORMAT_TIMEOUT_SECONDS=90`. No configures `RUN_WORKER_IN_WEB=false` mientras uses el worker embebido.

### `PRESAVED_FILES_JSON` para Memo's

El ID reservado `catalogo_pdf` no necesita `link`: el backend busca en runtime el
único catálogo vigente como `catalogos/memos.{pdf|jpg|jpeg|png|webp}` y usa su MIME
real para enviarlo como documento o imagen:

```json
[{"id":"catalogo_pdf","description":"Catálogo de Quesos Memo's; enviarlo cuando pidan el catálogo, precios generales o quieran ver todos los productos.","type":"document","filename":"Catálogo Memo's.pdf","caption":"Patrón, aquí tienes el catálogo de Quesos Memo's 🧀"}]
```

En Railway Memo's pega **solo el JSON anterior**, desde `[` hasta `]`, sin comillas
exteriores y sin barras `\`. Si se define desde Bash o un archivo `.env`, usa:

```bash
PRESAVED_FILES_JSON="[{\"id\":\"catalogo_pdf\",\"description\":\"Catálogo de Quesos Memo's; enviarlo cuando pidan el catálogo, precios generales o quieran ver todos los productos.\",\"type\":\"document\",\"filename\":\"Catálogo Memo's.pdf\",\"caption\":\"Patrón, aquí tienes el catálogo de Quesos Memo's 🧀\"}]"
```

Este valor pertenece únicamente al Railway Memo's. No reemplaces ni edites
`PRESAVED_FILES_JSON` en el Railway Tanaka live.

Después del primer deploy, entra al perfil Memo's de Lovable y sube el PDF o imagen real. Verifica en Supabase Storage que exista exactamente un objeto `catalogos/memos.{ext}` con la extensión correcta.

Si este Supabase Memo's fue creado con una versión anterior del bootstrap y el log
muestra `message_logs_role_check` al guardar `Archivos enviados`, ejecuta una vez
`supabase/enable_runtime_message_roles.sql`. Los proyectos nuevos no necesitan este
paso porque las mismas sentencias ya forman parte de `supabase/bootstrap.sql`.

## 5. Prompt para Lovable

Copia y pega este prompt completo en Lovable:

> Tenemos un dashboard existente con dos perfiles completamente separados: `tanaka` y `memos`. Modifica únicamente la capa server-side/proxy; no expongas secretos en el navegador ni en variables `VITE_*`.
>
> Crea una configuración server-only por perfil:
> - Tanaka: `TANAKA_DASHBOARD_BACKEND_URL`, `TANAKA_DASHBOARD_API_KEY`, `TANAKA_DASHBOARD_PASSWORD`.
> - Memo's: `MEMOS_DASHBOARD_BACKEND_URL`, `MEMOS_DASHBOARD_API_KEY`, `MEMOS_DASHBOARD_PASSWORD`.
>
> Cada URL debe apuntar a su Railway independiente y cada API key debe coincidir solo con `DASHBOARD_API_KEY` de ese Railway. Elige el backend exclusivamente a partir del perfil autenticado en servidor, nunca desde una URL o `client_name` libre enviado por el navegador.
>
> Para todas las rutas `current-si`, `generate-si-changes`, `format-and-save-si`, `si-history`, `current-catalog` y `upload-catalog`, inyecta en servidor el `client_name` fijo del perfil (`tanaka` o `memos`) y envía `X-Dashboard-API-Key` al backend correspondiente. Rechaza con 403 cualquier body, query o FormData cuyo `client_name` no coincida; idealmente ignora el valor del navegador y reconstruye la solicitud en servidor.
>
> No envíes al frontend las URLs Railway, API keys ni contraseñas. Mantén cookies/sesiones separadas y `httpOnly`, `secure`, `sameSite=lax`. No uses un único `DASHBOARD_BACKEND_URL` compartido. Conserva la UI y comportamiento actuales salvo lo necesario para este enrutamiento.
>
> Agrega una prueba server-side que demuestre: login Memo's solo llama al Railway Memo's con `client_name=memos`; login Tanaka solo llama al Railway Tanaka con `client_name=tanaka`; un intento cruzado devuelve 403; y ningún secreto aparece en el bundle del cliente. Al terminar, enumera archivos cambiados, variables exactas a crear en Lovable y pruebas ejecutadas.

En los secretos server-side de Lovable asigna:

- `TANAKA_DASHBOARD_BACKEND_URL`: URL Railway live actual de Tanaka.
- `TANAKA_DASHBOARD_API_KEY`: valor actual del Railway Tanaka.
- `TANAKA_DASHBOARD_PASSWORD`: contraseña actual del perfil Tanaka.
- `MEMOS_DASHBOARD_BACKEND_URL`: nueva URL Railway Memo's.
- `MEMOS_DASHBOARD_API_KEY`: nuevo `DASHBOARD_API_KEY` de Railway Memo's.
- `MEMOS_DASHBOARD_PASSWORD`: nueva contraseña del perfil Memo's.

## 6. Pruebas de aceptación y orden seguro

1. Desde Lovable/Memo's, lee la instrucción y confirma que menciona Quesos Memo's; desde Tanaka confirma que sigue mostrando Tanaka.
2. Sube el catálogo Memo's y comprueba que solo exista `catalogos/memos.{pdf|jpg|jpeg|png|webp}`; confirma que el catálogo Tanaka no cambió.
3. Escribe al WhatsApp Memo's “¿qué productos tienen?” y confirma que llega el PDF o imagen Memo's con el MIME correcto.
4. Prueba una cotización menor a $400.000: no debe hacer handoff.
5. Prueba una cotización igual/superior a $400.000: debe crear conversación en el inbox Memo's.
6. Envía una imagen: debe ir al inbox Memo's, nunca al de Tanaka.
7. Contesta desde Chatwoot y cierra el ticket; comprueba que el bot Memo's se reanuda.
8. Prueba follow-up con `FOLLOW_UP_TEST_DELAY_SECONDS=10`; después elimina esa variable.
9. Solo cuando todo pase anuncia el número Memo's. No hagas ningún redeploy de Tanaka durante esta transición.

## 7. Migración segura a Chatwoot self-hosted

| Despliegue | Base URL | Cuenta / inbox / token / webhook secret |
|---|---|---|
| Tanaka durante transición | `https://app.chatwoot.com` | Recursos Cloud actuales de Tanaka; registrar los cuatro valores antes de migrar. |
| Memo's self-hosted | Raíz HTTPS Railway self-hosted | Todos exclusivos de Memo's. |
| Tanaka self-hosted | La misma raíz HTTPS Railway | Cuenta, inbox, agente/token y webhook exclusivos de Tanaka. |

Orden obligatorio:

1. Despliega y valida Chatwoot self-hosted fuera de este repositorio.
2. Crea la cuenta, inbox, agente/token y account webhook aislados de Memo's.
3. Despliega esta versión endurecida del bot con todos los secretos de Memo's.
4. Apunta solo el bot Memo's a la raíz self-hosted.
5. Valida texto, imagen, respuesta del agente, adjunto y resolución/reanudación de Memo's; prueba también firma inválida y cuenta/inbox cruzados.
6. Crea recursos self-hosted separados para Tanaka.
7. Registra de forma segura las variables Cloud actuales de Tanaka: raíz, account ID, inbox ID, API token y webhook secret.
8. Cambia únicamente las variables Chatwoot del Railway Tanaka.
9. Valida el flujo completo de Tanaka y confirma que Memo's no recibió datos.
10. Conserva Chatwoot Cloud y sus recursos durante una ventana de rollback.

Las notificaciones push móviles se configuran en el stack Chatwoot, no en el bot. Como pruebas finales, confirma además que un Meta 400/401/429/5xx no programa follow-up y que ningún token aparece en logs.

### App móvil: no mezclar Chatwoot Cloud con el self-hosted

El correo de un agente **no une instalaciones de Chatwoot**. Una sesión de
`app.chatwoot.com` y una sesión de `https://chat.briosos.org` son cuentas en dos
bases de datos independientes, aunque ambas muestren `memos@briosos.org`. Por
eso un handoff visible en `chat.briosos.org` nunca aparecerá en una app que siga
conectada a Chatwoot Cloud; no es un problema del webhook ni del bot.

La corrección más simple para Memo's es:

1. Cerrar la sesión Cloud de la app móvil (o agregar otra cuenta si la versión
   instalada permite mantener varias).
2. En la pantalla de acceso elegir **Custom server / Self-hosted** e introducir
   solo `https://chat.briosos.org`, sin `/app`, `/api/v1` ni el dominio Railway
   del bot.
3. Iniciar sesión con el usuario y la contraseña creados dentro de esa
   instalación self-hosted. Tener el mismo correo en Cloud no reutiliza la
   contraseña ni la sesión Cloud.
4. Abrir la cuenta **Quesos Memo's**, habilitar las notificaciones del inbox y
   conceder a Chatwoot el permiso de notificaciones del sistema operativo.
5. Con la app cerrada, provocar un handoff nuevo. Confirmar primero que la
   conversación aparece en la app y luego que llega el push.

Para que la asignación no dependa del estado disponible ni de la política
Default del inbox, configura en Railway `CHATWOOT_ASSIGNEE_ID` con el ID numérico
del agente `memos@briosos.org`. El bot lo envía en la misma operación que crea la
conversación: no hay una segunda llamada ni una carrera entre “creada” y
“asignada”. Si se elimina la variable, se conserva el comportamiento anterior y
Chatwoot vuelve a decidir la asignación.

Con esa asignación determinista, la configuración menos invasiva en la app es:

* **Activar:** “A conversation is assigned to you” y “A new message is created
  in an assigned conversation”.
* **Desactivar:** “A new conversation is created”, porque generaría un segundo
  aviso del mismo handoff.
* Mantener menciones y SLA solo si se usan operativamente; no son necesarios
  para garantizar el aviso inicial.

Así cada handoff genera un aviso dirigido al responsable, mientras los mensajes
posteriores de ese cliente siguen notificándose sin alertar por conversaciones
ajenas o duplicar la notificación de creación.

Si la app instalada no ofrece servidor personalizado, o abre siempre
`app.chatwoot.com`, no se debe mover el bot a Cloud para solucionarlo. La salida
inmediata y de menor riesgo es instalar `https://chat.briosos.org` como PWA
desde Chrome/Safari y habilitar sus notificaciones. Esto conserva una sola
fuente de conversaciones y no requiere cambiar ninguna variable de Railway.

Si la conversación ya aparece en la app self-hosted pero el push no llega, la
conexión de la app quedó corregida y el problema restante pertenece al servicio
de push de la instalación Chatwoot. Revisar su configuración móvil/push y los
logs de Chatwoot; modificar `CHATWOOT_BASE_URL`, los webhooks de Meta o este bot
no puede reparar esa segunda capa.

## Rollback

Si Memo's falla, desconecta temporalmente solo su webhook de Meta o revierte su último deploy. Si Tanaka falla después de migrar, restaura únicamente sus variables Cloud registradas y su account webhook Cloud; no borres ni revoques recursos Cloud durante la ventana. No cambies las variables, Supabase, Redis, cola ni webhooks del otro negocio.
