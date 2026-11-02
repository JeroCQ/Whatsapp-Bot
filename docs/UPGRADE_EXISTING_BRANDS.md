# Actualizar un negocio existente sin recrear su proyecto

Este procedimiento sirve para desplegar una versión nueva del código en una marca
existente. No crea otra marca y no intercambia recursos entre Railway projects.

## 1. Registrar y respaldar

1. Anota commit, dominio, variables y estado de los webhooks actuales.
2. Crea un backup/snapshot del Supabase de esa marca.
3. No ejecutes `supabase/bootstrap.sql`: ese archivo es solo para proyectos vacíos.

## 2. Actualizar el Supabase existente

1. Abre `supabase/upgrade_existing_brand.sql`.
2. Ejecuta primero solo la consulta inicial de `chatwoot_conversation_id`
   duplicados. Debe devolver cero filas; el propio SQL también aborta si encuentra
   duplicados.
3. Ejecuta el archivo completo y exige `true` en todas las columnas de la consulta
   final. Es idempotente y conserva clientes, conversaciones y mensajes.
4. No ejecutes además `enable_runtime_message_roles.sql`: ya está incluido en el
   upgrade completo. Ese archivo pequeño queda para reparar únicamente el error de
   roles sin aplicar todavía el resto del upgrade.

Para el soporte de perfiles con usuario y teléfono oculto, ejecuta este upgrade
antes de actualizar el web y los workers de cada marca. Amplía de `varchar(25)` a
`text` las columnas `phone_number` de `customers`, `conversation_states` y
`message_logs` en una sola operación atómica, conservando datos, claves y relaciones.
La comprobación final `has_unbounded_whatsapp_identities` debe ser `true`.
PostgreSQL toma bloqueos de esquema durante esa operación: inclúyela en la ventana
de actualización del proyecto respaldado. No uses el antiguo upgrade específico
de Tanaka para esta capacidad; los proyectos nuevos ya la reciben en el bootstrap.

El identificador se conserva como llega de Meta, sin extraer un teléfono desde
un `wamid` ni relacionar clientes por username o por un número comunicado por chat.
Los consumidores de la API deben aceptar el valor opaco del campo `phone_number`.
Chatwoot lo vincula mediante un `identifier` estable del negocio y número Meta,
sin construir teléfonos `+CO.…`; cada Railway conserva sus recursos aislados.

## 3. Mantener la identidad del Railway

No copies el bloque completo de variables de otra marca. Conserva las credenciales
existentes y verifica solamente su correspondencia:

| Variable | Tanaka | Memo's |
| --- | --- | --- |
| `BUSINESS_ID` | `tanaka` | `memos` |
| `QUEUE_NAME` | `whatsapp-events-tanaka` | `whatsapp-events-memos` |
| `REDIS_URL` | Redis del proyecto Tanaka | Redis del proyecto Memo's |
| `GITHUB_SI_PATH` | `src/clients/tanaka/system_instruction.txt` o sin definir | `src/clients/memos/system_instruction.txt` o sin definir |
| `SUPABASE_URL` / `SUPABASE_SERVICE_ROLE_KEY` | Proyecto Tanaka | Proyecto Memo's |
| Meta y Chatwoot | IDs/tokens de Tanaka | IDs/tokens de Memo's |

Conserva el `PRESAVED_FILES_JSON` propio y el objeto `catalogos/{BUSINESS_ID}.*` de
cada Supabase. No copies la imagen Velvet ni cambies el catálogo vigente.

Los reintentos transitorios de Gemini se habilitan al desplegar el código y no
requieren migración SQL ni alteran datos existentes. Cada Railway puede conservar
los defaults (tres intentos totales, espera inicial de 1 segundo, tope de 8 y
jitter de 0,5) o definir sus propios `GEMINI_RETRY_ATTEMPTS`,
`GEMINI_RETRY_BASE_SECONDS`, `GEMINI_RETRY_MAX_SECONDS` y
`GEMINI_RETRY_JITTER_SECONDS`. Configúralos en el proyecto de cada marca; no copies
variables desde otro Railway como mecanismo de despliegue.

La compatibilidad con los errores reales `code/details` de `google-genai==1.28.0`
y los límites de espera tampoco requieren SQL ni variables nuevas obligatorias.
Los valores opcionales `GEMINI_REQUEST_TIMEOUT_SECONDS` (default `20`) y
`GEMINI_OPERATION_TIMEOUT_SECONDS` (default `60`) se aplican a cada instalación.
El segundo incluye semáforo y backoff; cada llamada SDK tiene un solo intento
interno. Un turno con audio puede ejecutar dos operaciones. HTTPX limita fases de
red, de modo que el presupuesto no es una garantía absoluta de tiempo de una
petición ya en vuelo. Mantener web y RQ del negocio en el mismo SHA; no aumentar
el timeout RQ ni reabrir automáticamente conversaciones en atención humana.

La corrección de PDF del 28 de octubre se amplía al
[relay genérico de archivos](GENERIC_MEDIA_RELAY.md) del 30 de octubre.
`CHATWOOT_MAX_DOCUMENT_BYTES` conserva default y máximo de 104857600 bytes
(100 MiB), ahora para todos los documentos salientes. El default genérico de
`CHATWOOT_MAX_ATTACHMENT_BYTES` pasa de 25 a 100 MiB; si un Railway tiene un valor
explícito menor, se sigue respetando. No se exige una variable nueva. Los archivos
salientes usan multipart por bloques y archivo temporal (hasta 100 MiB por
operación); no hace falta volumen persistente. Se elimina la dependencia de un
decodificador local para archivos entrantes y se conservan comprobaciones de
integridad, origen, cuenta e inbox. No modifica documentos ni requiere SQL; es
el mismo comportamiento en bootstrap nuevo y base existente.
Consultar [el informe y la verificación del 28 de octubre](HEALTHCHECK_2026_10_28.md)
antes del despliegue. Los diagnósticos de Gemini ahora distinguen indisponibilidad
de Google, límites de cuota, tiempo de espera y respuesta con formato inválido.

La corrección de textos de seguimiento también aplica a Tanaka, Memo's, Velvet y
negocios futuros. Se conserva el `follow_up_message` generado desde la instrucción
de cada `BUSINESS_ID`, incluido el seguimiento posterior a un catálogo; cuando falta,
se utiliza un mensaje general sin categorías de otra marca. Un campo explícitamente
vacío o null ahora cancela los seguimientos. El cambio histórico de texto por sí
solo no requería SQL; el paquete actual sí requiere el upgrade canónico para las
preferencias persistentes, `message_logs.metadata/event_key` y `handoff_deliveries`.
Despliega el mismo
commit en el web y los workers de cada Railway aislado: el worker actualizado
sustituye únicamente el antiguo texto fijo de panadería/postres en jobs ya encolados,
sin borrar colas ni cambiar otros mensajes personalizados. Los proyectos nuevos
siguen la misma política mediante `docs/NEW_BUSINESS_SETUP.md`.

Antes de este despliegue revisa [el procedimiento de auditoría de Memo's](MEMOS_AUDIT_BACKEND.md).
Incluye protección de jobs antiguos sin borrar Redis, separación al ejecutar,
resultados ambiguos de proveedores, casos pendientes de asesor y reversión mediante
`FOLLOW_UPS_ENABLED=false`. El SQL es aditivo: no borres históricos ni elimines las
columnas nuevas al revertir una aplicación. Comprueba `queue.worker_commits` en
`/health` y el SHA de web; un worker `unknown` o de otro SHA aún no está verificado.

En cada API inbox, configura su webhook URL hacia `/chatwoot-webhook` del Railway
de esa misma marca y guarda el `secret` del canal como
`CHATWOOT_API_INBOX_WEBHOOK_SECRET`. Es distinto del HMAC token de identidad y del
secreto de un account webhook. Mantener `CHATWOOT_ASSIGNMENT_MODE=automatic` y
`CHATWOOT_ASSIGNEE_ID` vacío es válido; la entrega no depende del modo de asignación.
Un administrador puede confirmar el secreto con
`GET /api/v1/accounts/{account_id}/inboxes/{inbox_id}` y tomando el campo `secret`
(nunca `hmac_token`). Después de entregar a Meta, el bot actualiza ese mensaje del
API inbox a `delivered`; si Meta falla, lo marca `failed` con un error genérico.

## 4. Desplegar y verificar

1. Despliega primero sin cambiar callbacks ni secretos.
2. Abre `/` y confirma el commit, `queue.enabled=true`, el nombre de cola correcto y
   al menos un worker.
3. Envía una conversación nueva y verifica en el Supabase de esa misma marca filas
   nuevas en `customers`, `conversation_states` y `message_logs`.
4. Pide el catálogo: debe enviarse una sola vez y debe poder guardarse un log con
   `role=system` sin error `message_logs_role_check`.
5. Fuerza un handoff y confirma que solo aparece en la cuenta/inbox Chatwoot de esa
   marca; resuelve el ticket y confirma la reactivación.
6. No pruebes Tanaka y Memo's con la misma cola Redis ni apuntando ambos números al
   mismo callback.
7. Comprueba un remitente con teléfono y otro con BSUID: ambos deben entrar a la
   cola, recibir texto y catálogo y poder pasar a su propio inbox Chatwoot. Revisa
   que el usuario con BSUID no genere `[WEBHOOK INVALID]` y que la respuesta use
   `recipient`. Un HTTP 200 de Meta confirma aceptación, no entrega al dispositivo;
   confirma también la recepción en la prueba del cliente.

## 5. Rollback

Si falla, revierte únicamente el Railway de esa marca al commit anterior. No reviertas
el SQL eliminando columnas o roles: las adiciones son compatibles con la versión
anterior. Si fuera necesario, desconecta temporalmente solo su callback Meta. No
modifiques el Railway, Supabase, Redis, número o Chatwoot de las otras marcas.
