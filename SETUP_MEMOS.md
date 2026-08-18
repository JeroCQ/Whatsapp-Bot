# Puesta en producción de Quesos Memo's (Railway + Supabase + WhatsApp + Lovable)

Esta instalación crea infraestructura totalmente separada de Tanaka. Solo se comparten el repositorio y la aplicación de Lovable. **No copies datos, tokens, Redis ni referencias `${{powerful-stillness.*}}` de Tanaka al proyecto Memo's.** Este código selecciona la marca con `BUSINESS_CLIENT=memos`.

## 0. Topología final

- Proyecto Railway **Quesos Memos**: servicio web del bot + Redis propio. Para el volumen inicial, el web usa el worker embebido.
- Proyecto Supabase **Quesos Memos**: base de conversaciones y bucket de catálogo propios.
- Meta: app/WABA/número de Memo's y webhook apuntando al dominio Railway de Memo's.
- Chatwoot: inbox de Memo's (puede estar en la misma instalación/cuenta, pero debe ser un inbox separado).
- Lovable: un perfil/login por marca; Tanaka llama al Railway de Tanaka y Memo's al Railway de Memo's, siempre desde rutas de servidor.

## 1. Supabase nuevo

1. En Supabase, crea un proyecto llamado `quesos-memos-prod` y guarda la contraseña de base de datos.
2. Abre **SQL Editor**, pega todo `supabase/bootstrap.sql` y ejecútalo. Debe terminar sin errores.
3. En **Storage**, crea un bucket llamado `catalogos` y márcalo **Public**. Limita MIME a `application/pdf` y ajusta el máximo de archivo al tamaño real del catálogo (el backend permite 100 MB por defecto).
4. En **Project Settings → API** copia:
   - Project URL → `SUPABASE_URL`.
   - La clave secreta/service-role → `SUPABASE_SERVICE_ROLE_KEY`. No uses anon/publishable.
5. No ejecutes el SQL sobre el Supabase de Tanaka.

## 2. WhatsApp/Meta nuevo

1. En Meta Business crea o selecciona la app de Memo's y agrega **WhatsApp**.
2. Agrega y verifica el número exclusivo de Memo's. Copia **Phone number ID** (no el número visible) a `WA_PHONE_NUMBER_ID`.
3. En **System Users**, crea un usuario de sistema, asígnale la app y WABA, concede `whatsapp_business_messaging` y `whatsapp_business_management`, y genera un token permanente → `WA_TOKEN`. No dejes el token temporal de prueba.
4. Genera localmente un secreto aleatorio para verificación, por ejemplo `openssl rand -hex 32` → `WA_VERIFY_TOKEN`.
5. Después de desplegar Railway, configura el callback como `https://DOMINIO-MEMOS.railway.app/webhook`, coloca el mismo `WA_VERIFY_TOKEN` y suscribe `messages`.
6. Envía un mensaje real y confirma en Railway que llega el webhook. Tanaka debe seguir apuntando exclusivamente a su dominio.

## 3. Chatwoot

1. Crea un inbox API separado llamado **WhatsApp Quesos Memo's**.
2. Obtén el ID numérico del inbox desde su URL/API → `CHATWOOT_INBOX_ID`.
3. Copia el ID numérico de la cuenta → `CHATWOOT_ACCOUNT_ID`.
4. Desde el perfil del agente/bot crea un access token → `CHATWOOT_API_TOKEN`.
5. Usa la URL raíz de la instalación, sin `/api/v1` al final → `CHATWOOT_BASE_URL` (por ejemplo `https://app.chatwoot.com`).
6. Configura en Chatwoot el webhook saliente hacia `https://DOMINIO-MEMOS.railway.app/chatwoot-webhook` para que el cierre por humano reactive el bot. Usa el inbox de Memo's, nunca el de Tanaka.

## 4. Railway nuevo

1. Crea un proyecto **quesos-memos-prod**, agrega un servicio desde este mismo repositorio/rama y agrega un servicio Redis al proyecto.
2. No pegues expresiones `${{powerful-stillness.VARIABLE}}`: apuntan al servicio/proyecto de Tanaka. En Memo's guarda valores propios o referencias a variables compartidas **dentro del proyecto nuevo**. La única referencia de servicio recomendada es `REDIS_URL=${{Redis.REDIS_URL}}`.
3. Usa el start command de `railway.json`. No crees worker separado inicialmente; el launcher inicia el worker embebido.
4. Agrega las variables de la tabla siguiente y despliega.

### Variables: eliminar, conservar o cambiar

| Variable actual | Acción en Memo's | Valor/origen |
|---|---|---|
| `BUSINESS_CLIENT` | **Agregar** | `memos`. Es la variable que selecciona prompt, catálogo, ruta de Storage y protección del dashboard. |
| `CATALOG_STORAGE_BUCKET` | Conservar | `catalogos`, nombre del bucket creado en el Supabase nuevo. |
| `catalogo_memos` | Cambiar | JSON de abajo. Define el archivo permitido para Gemini; el enlace real se reemplaza por `{SUPABASE_URL}/storage/.../memos.pdf`. |
| `catalogo_tanaka` | **Eliminar** | Memo's no lo lee. Se queda solo en Railway Tanaka. |
| `PRESAVED_FILES_JSON` | **Eliminar** | No lo usa esta instalación de marca; `catalogo_memos` es la fuente explícita. |
| `SUPABASE_URL` | Cambiar | Project URL del Supabase Memo's. |
| `SUPABASE_KEY` | **Reemplazar/eliminar** | Usa `SUPABASE_SERVICE_ROLE_KEY` en su lugar. Si temporalmente conservas `SUPABASE_KEY`, debe ser service-role, nunca anon. |
| `SUPABASE_SERVICE_ROLE_KEY` | **Agregar** | Clave secreta/service-role del Supabase Memo's. |
| `WA_PHONE_NUMBER_ID` | Cambiar | Phone number ID de Meta para el número Memo's. |
| `WA_TOKEN` | Cambiar | Token permanente del system user de Memo's. |
| `WA_VERIFY_TOKEN` | Cambiar | Secreto aleatorio nuevo; el mismo valor se pega en Meta. |
| `CHATWOOT_ACCOUNT_ID` | Cambiar si corresponde | ID de la cuenta que contiene el inbox Memo's. Puede coincidir con Tanaka si comparten cuenta. |
| `CHATWOOT_INBOX_ID` | Cambiar | ID del inbox exclusivo de Memo's. |
| `CHATWOOT_API_TOKEN` | Cambiar/revisar | Token con acceso al inbox Memo's. Puede ser común, pero uno separado reduce impacto. |
| `CHATWOOT_BASE_URL` | Conservar si comparten Chatwoot | URL raíz de la instalación. |
| `GEMINI_API_KEY` | Conservar o separar | Clave de Google AI Studio. Una clave/proyecto separado permite cuotas y rotación independientes. |
| `GOOGLE_API_KEY` | **Eliminar** | El SDK usa `GEMINI_API_KEY`; tener ambas puede seleccionar la equivocada. |
| `GEMINI_DASHBOARD_MODEL` | Conservar | Puede omitirse para usar el default del código; si se define, usa un modelo válido de la cuenta. |
| `GITHUB_TOKEN` | Conservar/cambiar | Fine-grained token con **Contents: Read and write** sobre este repo; lo usa el dashboard para guardar ambos SI. |
| `DASHBOARD_API_KEY` | Cambiar | Secreto aleatorio exclusivo de backend Memo's (`openssl rand -hex 32`). Debe coincidir con el secreto Memo's en Lovable. |
| `DASHBOARD_CORS_ORIGINS` | Eliminar o dejar vacío | Lovable debe llamar por una ruta server-side; el navegador no debe llamar Railway directamente. |
| `DASHBOARD_EXTERNAL_TIMEOUT_SECONDS` | Conservar | `30`, o eliminar para usar default. |
| `DASHBOARD_HISTORY_MAX_PAGE_SIZE` | Conservar | `50`, o eliminar para usar default. |
| `DASHBOARD_MAX_CATALOG_MB` | Conservar | `100` o el límite deseado. |
| `DASHBOARD_MAX_PDF_BYTES` | **Eliminar** | Es legado/no participa en el upload actual; manda `DASHBOARD_MAX_CATALOG_MB`. |
| `DASHBOARD_MAX_TEXT_CHARS` | Conservar | `100000`, o eliminar para usar default. |
| `DASHBOARD_REQUESTS_PER_MINUTE` | Conservar | `30`, o eliminar para usar default. |
| `DASHBOARD_STORAGE_TIMEOUT_SECONDS` | Conservar | `180`, o eliminar para usar default. |
| `DASHBOARD_FORMAT_TIMEOUT_SECONDS` | **Agregar recomendado** | `90`. Da más tiempo al formateo de SI. |
| `REDIS_URL` | Cambiar | `${{Redis.REDIS_URL}}`, referenciando el Redis del proyecto Memo's. |
| `QUEUE_NAME` | Cambiar | `whatsapp-events-memos`. Debe ser exclusivo incluso si algún día se comparte Redis. |
| `RUN_WORKER_IN_WEB` | Agregar/omitir | Omítelo (default `true`) para el worker embebido. No agregues un worker Railway separado a la vez. |
| `GITHUB_OWNER`, `GITHUB_REPO`, `GITHUB_BRANCH` | Normalmente eliminar | Railway los inyecta desde el repo conectado. Solo son fallback fuera de Railway. |
| `KOMMO_PRIVATE_TOKEN`, `KOMMO_BASE_URL`, `KOMMO_REQUEST_TIMEOUT_SECONDS` | **Eliminar** | Este código no integra Kommo. |
| `PIN` | **Eliminar** | No se usa; el acceso del dashboard vive en Lovable. |

Valor recomendado de `catalogo_memos` (una sola línea JSON en Railway):

```json
[{"id":"catalogo_pdf","description":"Catálogo oficial de Quesos Memo's; enviarlo cuando pidan el catálogo, precios generales o quieran ver todos los productos.","type":"document","link":"https://example.com/placeholder.pdf","filename":"catalogo-quesos-memos.pdf","caption":"Patrón, aquí tienes el catálogo de Quesos Memo's 🧀"}]
```

El placeholder debe ser HTTPS válido, pero no es la fuente final. En ejecución se reemplaza por el `memos.pdf` del Supabase de este deployment.

## 5. Primer despliegue y catálogo

1. Abre `https://DOMINIO-MEMOS.railway.app/`. Debe mostrar `status: ok`, `business_client: memos`, `queue.enabled: true` y al poco tiempo `workers_seen >= 1`.
2. En logs confirma `[WORKER] Starting RQ worker for queue=whatsapp-events-memos`.
3. Configura Lovable como indica la sección siguiente y entra con el perfil Memo's.
4. Sube el PDF desde el perfil Memo's. Debe quedar en el Supabase nuevo como `catalogos/memos.pdf`.
5. Comprueba que la URL pública abre el PDF y pide “muéstrame el catálogo” por WhatsApp. El documento debe llegar con nombre de Memo's.

## 6. Prompt exacto para Lovable

Pega este pedido en Lovable (sustituye solo si tus rutas actuales tienen otros nombres):

> Actualiza la integración del dashboard para soportar dos backends completamente aislados, conservando una sola interfaz y los perfiles `tanaka` y `memos`. Haz primero un inventario de las rutas server-side actuales y adapta esas rutas; no hagas llamadas directas desde el navegador a Railway ni expongas secretos con prefijo `VITE_`.
>
> Crea configuración server-only por cliente: `TANAKA_DASHBOARD_BACKEND_URL`, `TANAKA_DASHBOARD_API_KEY`, `MEMOS_DASHBOARD_BACKEND_URL`, `MEMOS_DASHBOARD_API_KEY`, `TANAKA_DASHBOARD_PASSWORD` y `MEMOS_DASHBOARD_PASSWORD`. Después del login, deriva el cliente exclusivamente comparando la contraseña en servidor y guarda una sesión segura HttpOnly/Secure/SameSite; el navegador no puede elegir o sobrescribir `client_name`.
>
> Para cada request autenticado a `current-si`, `generate-si-changes`, `format-and-save-si`, `si-history`, `current-catalog` y `upload-catalog`, selecciona URL y API key con un mapa server-only según el cliente de la sesión. Inyecta en servidor el `client_name` (`tanaka` o `memos`) en query/body/FormData, reemplazando cualquier valor enviado por el browser. Envía al Railway elegido el header `X-Dashboard-API-Key`. Nunca envíes la API key ni URL privada al cliente.
>
> Tanaka debe llamar solo a `TANAKA_DASHBOARD_BACKEND_URL`; Memo's debe llamar solo a `MEMOS_DASHBOARD_BACKEND_URL`. Agrega `current-catalog` a las rutas permitidas si falta. Conserva carga multipart de PDF sin convertirla a JSON y conserva status/body del upstream. Implementa timeout y mensajes de error sin imprimir contraseñas, API keys, tokens, contenido del SI ni PDF.
>
> En la UI muestra claramente la marca autenticada y añade “Cerrar sesión”. Al cambiar de perfil, invalida la sesión anterior. No mezcles caché, estado de formulario, historial, SI ni catálogo entre marcas; incluye el cliente en todas las query keys/cache keys y limpia el estado al logout.
>
> Agrega pruebas que demuestren: (1) contraseña Tanaka selecciona únicamente backend Tanaka; (2) contraseña Memo's selecciona únicamente backend Memo's; (3) un body/query manipulando `client_name` es ignorado o rechazado; (4) las keys jamás aparecen en el bundle o respuesta del navegador; (5) los uploads conservan el PDF y llegan al backend correcto. Entrega una lista de archivos modificados y los nombres exactos de secretos que debo configurar, sin mostrar sus valores.

En los secretos server-side de Lovable configura:

- `TANAKA_DASHBOARD_BACKEND_URL`: dominio Railway actual de Tanaka.
- `TANAKA_DASHBOARD_API_KEY`: mismo valor que `DASHBOARD_API_KEY` de Railway Tanaka.
- `MEMOS_DASHBOARD_BACKEND_URL`: dominio Railway nuevo de Memo's.
- `MEMOS_DASHBOARD_API_KEY`: mismo valor que `DASHBOARD_API_KEY` de Railway Memo's.
- Las dos contraseñas de perfil deben ser distintas y no deben coincidir con ninguna API key.

## 7. Pruebas de aceptación antes de anunciarlo

1. Login Tanaka: lee SI/catálogo Tanaka y nunca muestra Memo's.
2. Login Memo's: lee `src/clients/memos/system_instruction.txt` y sube a `catalogos/memos.pdf` del Supabase Memo's.
3. En cada Railway, `/` muestra la marca correcta y cola correcta.
4. Mensaje de saludo, pregunta de producto concreto, solicitud de catálogo y cotización menor a $400.000: los atiende el bot.
5. Cotización igual/superior a $400.000, solicitud mayorista, foto, reclamo y solicitud de humano: crean handoff en el inbox Memo's.
6. Pedido pendiente: llega follow-up; si el cliente responde antes, se cancela.
7. Transferencia: entrega la cuenta solo tras resumen confirmado; la foto escala y el bot no declara el pago aprobado.
8. Cierra la conversación en Chatwoot y verifica que el bot se reactive.
9. Repite una prueba corta en Tanaka para demostrar que el despliegue nuevo no alteró su número, Supabase, inbox, Redis ni catálogo.
