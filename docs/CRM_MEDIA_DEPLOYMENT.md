# CRM multimedia: actualización coordinada y manual

Esta es la guía única de esta entrega para el bot, CRM y frontend real de Lovable.
La corrección posterior de nombres/orden/actualización de bandeja se entrega en
`fix/crm-inbox-freshness` de ambos repositorios, con PR backend hacia
`feat/crm-media-delivery` y frontend hacia `main`. Para esa corrección, actualizar
primero el exportador del bot del negocio autorizado, después API y sync al mismo
commit y finalmente el frontend. No requiere SQL ni migraciones nuevas; conserva
`0004_delivery_receipts`. En el worker, usar `SYNC_INTERVAL_SECONDS=5` si había
un valor explícito de 30. No aplicar cambios pendientes de variables de otros
negocios. Las versiones y ramas de la entrega multimedia original se mantienen
abajo como referencia histórica, no como el commit de esta corrección.
No crea otro CRM, otra base, otra autenticación ni otro emisor de WhatsApp.
La ampliación a Tanaka, Memo’s y Velvet está autorizada en el código por el
registro configurado; no se ha activado ni desplegado ningún negocio desde Codex.

## Versiones y servicios

Código backend validado: **af357d3479c7a715a3beb1bc6c53b687e09cc263**. Código frontend validado:
**43a935cd135cc08dfeb053e0a00d8bbcf4790e75**. La rama de trabajo en ambos repositorios es
`feat/crm-media-delivery`. Los PR apuntan respectivamente a `crm-staging-safety`
y `main`; no a `43-CRM`. El commit posterior que añade esta guía no cambia código.

| Servicio existente | Repositorio | Rama de integración | Root directory | Arranque que se conserva |
| --- | --- | --- | --- | --- |
| `bot-briosos-web` | `JeroCQ/Whatsapp-Bot` | `crm-staging-safety` tras revisión del PR | `/` | `railway.json`: precheck `py_compile` y `python run_railway.py` |
| `bot-tanaka-web` | mismo | misma versión CRM compatible, por actualización explícita del operador | `/` | mismo arranque existente |
| `bot-velvet-web` | mismo | misma versión CRM compatible, por actualización explícita del operador | `/` | mismo arranque existente |
| `Memos-Whatsapp-Bot` | mismo | misma versión CRM compatible, por actualización explícita del operador | `/` | mismo arranque existente |
| `crm-staging-api` | mismo | `crm-staging-safety` tras revisión del PR | `/crm` | `crm/railway.toml`, Dockerfile existente; preDeploy `alembic upgrade head`; `uvicorn crm_service.api:create_app --factory --host 0.0.0.0 --port ${PORT:-8080} --no-access-log` |
| `crm-staging-sync` | mismo | mismo commit backend que API | `/crm` | `crm/railway.worker.toml`: `python -m crm_service.worker` |
| Hosting Lovable actual | `JeroCQ/chatty-bot-command-center` | `main` tras revisión del PR | `/` | build existente `bun run build`; runtime Cloudflare gestionado por el hosting |

No crear workers adicionales del bot ni cambiar Redis/colas. Se conserva el modo
actual de `run_railway.py`, incluido su worker embebido cuando corresponda.
No modificar los otros servicios históricos `crm-api`/`crm-sync` para esta entrega.
El dominio que conserva un nombre antiguo no demuestra qué negocio atiende:
comprueba `business_id` de `/health` frente al servicio y a su Supabase propio.

## Antes de pulsar Redeploy

Los nombres de variables, con sus valores ocultos, no demuestran que la conexión
esté lista. Verificar privadamente estos valores y que cada servicio despliega
el código indicado arriba. No pegar contraseñas ni tokens en chats o logs.

En esta implementación **`CRM_WEBHOOK_URL` no se utiliza**. La recepción sigue
en el webhook existente de cada bot. El CRM lee incrementalmente su exportación
autenticada GET mediante `crm-staging-sync`; no se cambia el webhook de Meta.

### Cada bot que se incorpora

Conservar su `BUSINESS_ID`, Supabase, número Meta, tokens, catálogo, instrucciones,
Redis y cola propios. Para control humano CRM se requiere:

```dotenv
HUMAN_SUPPORT_PROVIDER=crm
CRM_CONTROL_ENABLED=true
CRM_SOURCE_READ_ENABLED=true
CRM_BOT_CONTROL_SECRET=<secreto-control-existente-de-este-bot>
CRM_SOURCE_READ_SECRET=<secreto-distinto-solo-lectura-de-este-bot>
WA_APP_SECRET=<app-secret-de-la-app-Meta-de-este-bot>
CRM_MEDIA_ENABLED=true
```

`CRM_MEDIA_ENABLED` es el nuevo interruptor de archivos; por defecto es `false`.
El código no impone Briosos como único negocio. `CRM_MEDIA_ALLOWED_BUSINESSES`
es opcional y normalmente se deja sin definir; si se define, limita los IDs
permitidos. `CRM_MEDIA_BUCKET` es opcional, por defecto `crm-private-media`.
No se necesitan claves nuevas de almacenamiento: solo el bot usa las suyas
existentes de Supabase. Nunca copiarlas al CRM o al navegador.

Cambiar a proveedor `crm` es una decisión de atención del negocio, no un efecto
de agregar `CRM_BOT_CONTROL_SECRET`. Desactiva las operaciones de Chatwoot en ese
bot, conserva las pausas humanas y usa la autoridad CRM del bot. No borres las
variables antiguas para hacer esta actualización; pueden ser necesarias para
una recuperación coordinada. No cambies de proveedor con casos humanos inciertos.

### `crm-staging-api`

```dotenv
CRM_BUSINESSES=briosos,tanaka,memos,velvet
CRM_BOT_TEST_MODE=false
CRM_MEDIA_ENABLED=true
NOTIFICATIONS_ENABLED=false
```

Conservar `CRM_DATABASE_URL`, `CRM_JWT_SECRET` y `CRM_SYNC_SECRET`. Para cada ID
de `CRM_BUSINESSES` configurar sus variables ya existentes:

```dotenv
TANAKA_BOT_API_URL=https://<origen-correcto-del-bot-tanaka>
TANAKA_HUMAN_CONTROL_ENABLED=true
TANAKA_BOT_CONTROL_SECRET=<mismo-control-secret-de-ese-bot>
```

Repetir el prefijo `BRIOSOS_`, `MEMOS_` y `VELVET_` para sus propios destinos y
secretos. No reutilizar un destino o secreto de otro negocio. La API no necesita
claves administrativas de Supabase ni el token Meta. El permiso multimedia se
hereda de `CRM_BUSINESSES` y de la preparación del bot; no hay que crear una
segunda lista ni otra contraseña. Una lista explícita
`CRM_MEDIA_ALLOWED_BUSINESSES` solo sirve para restringir voluntariamente.

### `crm-staging-sync`

La lista que se pegó del API no confirma la configuración del worker. Comprobar:

```dotenv
CRM_BUSINESSES=briosos,tanaka,memos,velvet
CRM_SOURCE_MODE=bot_api
CRM_BOT_TEST_MODE=false
NOTIFICATIONS_ENABLED=false
TANAKA_BOT_API_ENABLED=true
TANAKA_BOT_API_URL=https://<origen-correcto-del-bot-tanaka>
TANAKA_BOT_SOURCE_SECRET=<mismo-CRM_SOURCE_READ_SECRET-de-ese-bot>
```

Repetir para los demás prefijos. El worker usa el mismo PostgreSQL CRM que el
API, y sus secretos CRM existentes. No necesita secretos de control humano ni
claves administrativas de los Supabase de origen.

### Frontend Lovable

Se reutiliza el acceso Admin ya existente. Conservar las variables privadas
`PASSWORD_ADMIN`, `CRM_BACKEND_URL`, `CRM_JWT_SECRET`, `CRM_PILOT_OPERATOR_ID`
y `CRM_PILOT_ENABLED=true`. Ninguna lleva `VITE_`. No existe contraseña personal
nueva. `CRM_PILOT_BUSINESS_ID` solo es una preferencia inicial, no una restricción
por marca. El registro Admin existente incluye los cuatro negocios; para futuros
IDs se conserva `CRM_BUSINESS_IDS`. El selector existente cambia el negocio.

No hace falta pedir a Lovable otra implementación ni gastar créditos para
levantar la restricción: el cambio está en el repositorio conectado. El operador
solo integra el PR y usa el mecanismo de publicación existente cuando el backend
esté preparado. El actor técnico fijo no acredita identidad individual si varias
personas comparten la contraseña Admin.

## Almacenamiento y migraciones

Los archivos permanentes se guardan en un bucket **privado** del Supabase propio
del bot, con metadatos en `crm_media_assets` y claves que incluyen el negocio.
El disco local se usa únicamente como buffer temporal acotado y para conversión.
No se usan URLs temporales de Meta como historial permanente.

1. Hacer backup del PostgreSQL CRM y de cada Supabase que se va a actualizar.
2. Sobre cada Supabase seleccionado, revisar el estado previo. Si ya dispone del
   esquema CRM de `4c3596f`, aplicar la actualización incremental idempotente
   [`crm_media_upgrade.sql`](../supabase/crm_media_upgrade.sql). Si se incorpora
   por primera vez al CRM, aplicar el canónico
   [`upgrade_existing_brand.sql`](../supabase/upgrade_existing_brand.sql), que
   incluye también las columnas previas necesarias. No ejecutar el script de un
   proyecto en el Supabase de otro negocio. Ambos conservan datos; no hace falta
   ejecutar los dos. Para un proyecto nuevo corresponde
   [`bootstrap.sql`](../supabase/bootstrap.sql). Las secciones de control y
   multimedia son idénticas y fueron probadas con datos previos.
3. Para el PostgreSQL **propio del CRM**, conservar el preDeploy existente del API:
   desde `/crm`, `alembic upgrade head`. Se ejecuta al actualizar el API al código
   compatible y lleva a `0004_delivery_receipts`, preservando datos. No adelantar
   esa revisión mientras se pretenda mantener un API antiguo que exige `0003`;
   coordinar la ventana de actualización. No ejecutar Alembic contra el Supabase
   del bot. Actualizar API y sync con el mismo código.
4. No activar archivos si el bucket está público, falta el esquema, falta espacio
   o la cuenta de servicio no puede acceder. `/media-capabilities` devuelve el
   motivo; la UI no debe aparentar que el almacenamiento existe.

La nueva dependencia operativa es ese bucket privado y la tabla en el Supabase
existente; el SQL está entregado, **su creación real no se realizó en esta tarea**.
Si el proyecto no dispone de Storage utilizable, multimedia permanece desactivada
hasta habilitarlo. No se ha provisionado infraestructura alternativa.

## Orden de actualización y comprobación

1. Revisar los dos PR y sus commits, todavía sin merge: las ramas conectadas pueden
   disparar auto-deploy. Codex no hace merge ni cambia ramas o variables de Railway.
2. Hacer backups y aplicar primero el SQL aditivo de cada Supabase elegido. Tener
   preparados los valores privados para el código compatible; no iniciar un bot
   antiguo esperando que variables nuevas añadan endpoints que no implementa.
3. Integrar manualmente el PR backend cuando el esquema del piloto esté preparado.
   Actualizar un bot a la vez, comenzando por Briosos de pruebas. Mantener intactos
   los demás servicios hasta su propia actualización explícita; no sustituir sus
   instrucciones, precios, números o colas por los de Briosos.
4. Actualizar `crm-staging-api` y `crm-staging-sync` al mismo backend compatible.
   `/ready` debe confirmar la revisión `0004_delivery_receipts`.
5. Comprobar privadamente la identidad y preparación, sin enviar mensajes:

   ```bash
   curl --fail --silent "$BOT_URL/health"
   curl --fail --silent -H "X-CRM-Bot-Secret: $CRM_BOT_CONTROL_SECRET" \
     "$BOT_URL/internal/crm/v1/businesses/$BUSINESS_ID/media-capabilities"
   ```

   Exigir `business_id` correcto, `media_enabled=true` y `storage_ready=true`.
   No usar `curl -v` ni compartir cabeceras. Un servicio equivocado, una clave
   incorrecta o un esquema ausente no se corrigen cambiando URLs desde el navegador.
6. Si hace falta avanzar el baseline, usar desde el worker existente:

   ```bash
   python -m crm_service.worker --once --business briosos
   ```

   Es un paso acotado; repetir hasta baseline completo revisando `/v1/sync-status`.
   Usar el ID correspondiente para los otros negocios. No bajar límites de
   aislamiento, copiar claves administrativas o marcar un baseline parcial como
   completo. Un negocio fallido no debe bloquear a los demás.
7. Solo con el backend preparado, integrar el PR frontend y publicar por el
   procedimiento habitual (su sincronización puede ser automática). Entrar con
   Admin y comprobar cada negocio seleccionado, capacidades y errores. No hay otro
   servicio web o pantalla que crear.
8. Realizar la prueba física siguiente con remitente/destinatario autorizados.

**Para comenzar el redeploy controlado:** primero código revisado, backup, esquema
compatible y valores de configuración verificados; comenzar por Briosos de pruebas.
No hace falta una prueba física anterior a ese despliegue. **Para habilitar uso
con clientes en cada negocio:** exigir después identidad correcta, `/ready`,
almacenamiento privado preparado y baseline completo, y completar la aceptación
física. Los nombres de las variables pegadas no acreditan esos valores. Esta
guía permite hacer la intervención completa sin encargar diseño pendiente a otra IA.

## Formatos, límites y contrato

No hay límite local de duración de la grabación, megapíxeles o longitud de nombre
añadido como si fuera una restricción de Meta. El selector no descarta archivos
por su extensión o por un MIME vacío del navegador. El bot inspecciona bytes y
codecs; los MIME genéricos no hacen pasar HTML por PDF.

Se implementan JPEG/PNG, audios AAC/AMR/MPEG/MP4/OGG Opus, videos MP4/3GP H.264 con
AAC cuando hay audio, documentos PDF/texto/Office y stickers WebP. Límites de
referencia de Meta: imágenes 5 MB; audios/videos 16 MB; documentos 100 MB; sticker
estático 100 KB y animado 500 KB con las dimensiones requeridas por Meta. Los
archivos no identificados se conservan como documentos opacos, siempre descargables
y nunca ejecutados en el origen del panel. Meta decide su aceptación en la carga
de medios; una negativa se muestra como error confirmado y no se envía el mensaje.
Esto no promete que Meta admita cualquier archivo.

Las grabaciones WebM del navegador se convierten a OGG/Opus para el transporte.
Se conserva el original y se ofrece reproducción AAC/MP4 cuando el navegador no
admite OGG. La transcripción es complementaria: su fallo no elimina el original.
Los mensajes históricos sin identificador de medio recuperable no pueden inventarse.

- Subida: `POST /v1/conversations/{business}/{phone}/attachments` con cuerpo binario,
  `X-Upload-Size`, MIME e ID de carga; no JSON/base64. `upload_id`, `filename`,
  `expected_version` y `voice_note` son parámetros explícitos.
- Consulta y contenido: `GET .../attachments/{uuid}` y `GET .../{uuid}/content`,
  con rangos 206/416 y variante `original|playback`.
- Envío: el endpoint `messages` existente admite `attachment_id` con texto/caption
  cuando el tipo lo permite. Revalida propietario, pausa y versión al enviar.
- El proxy del panel emite permisos de lectura de cinco minutos para un único
  negocio, conversación, adjunto y variante. Son permisos derivados de Admin,
  no otro login: usan secretos ya existentes y se invalidan al rotar Admin. No
  contienen el JWT general del CRM ni tokens de Meta/Storage.
- `message-updates` y `delivery_cursor` actualizan recibos del mensaje original
  fuera de la última página. Los eventos técnicos se conservan en la base para
  auditoría, pero no son burbujas. `read` no vuelve a `delivered` o `accepted`.

Subir un archivo no significa enviarlo. `accepted` significa que Meta aceptó el
mensaje, no que se entregó. `delivered`/`read` requieren recibo. Un timeout produce
`unknown`, nunca un reenvío automático: consultar el mismo `command_id`.
Si se pierde el control durante la carga, el envío se rechaza aunque el archivo
ya haya quedado archivado. Cambiar cuenta/negocio o salir cancela consultas,
reproducción y grabación, libera el micrófono y descarta respuestas antiguas.

Fuentes oficiales consultadas y límite de acceso: la web vigente
`https://developers.facebook.com/docs/whatsapp/cloud-api/reference/media`
devolvió 403 en el proxy del entorno. Se contrastaron referencias oficiales
accesibles de Meta: `fbsamples/whatsapp-api-examples@de70ee908a67026e642aaee3703d20464e2a9466`
(`media-messages-js/complete-app/helper/validations.js`, límites en bytes) y
`WhatsApp/WhatsApp-Nodejs-SDK@58ca3d5fceea604e18393734578d9a7944a37b15`
(audio/video/documentos). Son archivos de 2023; no se presentan como verificación
de la documentación vigente de 2026. La aceptación del proveedor actual y del
dispositivo físico sigue pendiente, sin falsear los resultados simulados.

## Rollback

Primero desactivar `CRM_MEDIA_ENABLED` en API y bots afectados: se ocultan nuevas
cargas conservando texto, control y conversaciones existentes. Mantener la versión
compatible con la migración `0004`; no volver al API `0003` contra una base `0004`
sin un plan de restauración del backup. No ejecutar downgrade destructivo de
recibos ni borrar tablas/objetos para recuperar una UI.

No liberar pausas ni cambiar a un bot anterior sin autoridad CRM mientras haya
casos humanos o comandos `unknown`; podría reactivar IA incorrectamente.
Conservar los archivos privados y la base para recuperación. Cerrar/devolver a
IA solo mediante el control existente cuando el resultado del envío sea conocido.
Una restauración completa de backup requiere coordinar código y ambas bases;
nunca volver a enviar automáticamente los comandos previos.

## Aceptación real pendiente

Con un contacto y un número autorizados, por negocio:

- Cliente envía audio y PDF; abrir/escuchar/descargar sus originales en CRM.
- Handoff y toma de control confirmada; ninguna respuesta o seguimiento de IA
  simultáneo. Perder control durante una carga debe impedir enviar.
- Enviar audio y PDF desde CRM y comprobar recepción física, sin duplicados.
- Desde iPhone y Android: permiso de micrófono tras pulsar grabar, escucha previa,
  cancelar y volver a grabar, enviar; micrófono liberado al terminar/salir.
- Ver `accepted → delivered → read` debajo del mensaje correcto, incluso al cargar
  mensajes antiguos. No burbujas `DELIVERY_STATUS`, sí handoff y cierre.
- Cambiar de negocio con el mismo teléfono: archivos, borradores y estados aislados.
- Cerrar y devolver a IA; el siguiente mensaje vuelve a ser atendido por el bot.

Las pruebas automatizadas y de navegador de esta entrega usan proveedores ficticios
y PostgreSQL desechable. No son una prueba física de WhatsApp, iPhone o Android,
ni una afirmación de que los cuatro despliegues actuales ya estén listos.

## Evidencia de esta entrega y comandos locales

- Bot: 1.107 pruebas correctas, tres omitidas y un fallo previo confirmado en
  `test_memos_instruction_is_available_to_the_selected_deployment` (espera una
  cuenta bancaria ausente de la instrucción existente). Esa instrucción y prueba
  son idénticas a la base; no se cambió el negocio para ocultar el fallo.
- CRM: 259 pruebas correctas en PostgreSQL desechable; Alembic upgrade desde
  datos anteriores, metadata parity y roundtrip comprobados. Ruff y mypy de
  22 archivos correctos. Después del último ajuste de nombres se repitieron
  49 pruebas de media/recibos/migración y 23 de contrato.
- Integración SQL/routers: 14 aceptaciones previas correctas y 11 casos multimedia
  correctos (cuatro tipos en ambos sentidos, recibos, timeout, control perdido),
  con Meta/Storage simulados. Se ejecutó el SQL incremental completo dos veces
  sobre datos previos; los bloques fresh/upgrade/incremental son idénticos.
- Frontend: 109 pruebas de la suite, 33 del proxy y ocho de helpers correctas;
  TypeScript, lint del código cambiado y build Cloudflare correctos.
- workerd nativo: 14 pruebas correctas incluyendo subida binaria multiempresa,
  rangos y permisos de contenido. Runtime local `1.20260730.1`, Miniflare
  `4.20260730.0`, fecha efectiva `2026-08-06`; el build generado usa
  `2026-11-04`, más reciente que ese binario local. No equivale a prueba del hosting.
- Chromium móvil simulado: reproducción nativa de imagen/audio/video, PDF, rangos,
  cuatro subidas binarias, progreso/errores, recibos antiguos, aislamiento al cambiar
  negocio, pérdida de control y grabación/escucha/cancelación/liberación del micrófono.
  Se corrigieron dos referencias React que vaciaban el `src` del reproductor.

Comandos exactos, con PostgreSQL **local desechable** ya creado:

```bash
# Desde Whatsapp-Bot; esta suite no toca negocios reales.
.venv/bin/python -m pytest -q tests
CRM_TEST_DATABASE_URL=postgresql+psycopg://agent@127.0.0.1:55432/crm_test_media_full \
  PYTHONPATH=.:crm:tests:crm/.venv/lib/python3.12/site-packages \
  .venv/bin/python -m pytest -q pilot_tests/acceptance_pilot.py pilot_tests/acceptance_media.py
# Desde Whatsapp-Bot/crm
CRM_TEST_DATABASE_URL=postgresql+psycopg://agent@127.0.0.1:55432/crm_test_media_api .venv/bin/python -m pytest -q tests
.venv/bin/ruff check crm_service tests
.venv/bin/mypy crm_service
# Desde chatty-bot-command-center
bun test
node --experimental-strip-types src/lib/crm-pilot.server.test.mjs
node --experimental-strip-types src/lib/crm-pilot-media.test.mjs
bunx tsc --noEmit
bun run build
MINIFLARE_PATH=/tmp/command-center-workerd-tools/node_modules/miniflare node --test tests/workerd/crm-pilot.workerd.mjs
BASE_URL=http://127.0.0.1:8092 CHROMIUM_PATH=/usr/bin/chromium python3 tests/e2e/crm_media_e2e.py
```

El harness workerd documenta la instalación desechable de la herramienta; la prueba
de navegador requiere el dev server loopback con `PASSWORD_ADMIN=synthetic-admin-only`.
Intercepta sus APIs y bloquea HTTP externo; usa WebAudio como micrófono ficticio.
Ninguna de estas cifras acredita recepción física, disponibilidad del bucket real
o corrección de los valores ocultos en los cuatro servicios de Railway.
