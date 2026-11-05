# Contrato servidor frontend ↔ CRM

La atención operativa del piloto se especifica en 
[CRM_PILOT_HANDOFF.md](../../docs/CRM_PILOT_HANDOFF.md): `GET C/control`,
`GET C/commands/{command_id}`, `POST C/take`, `POST C/messages`, `POST C/close`,
cuerpos, respuestas, estados inciertos y conexión de Lovable. Este documento
conserva el contrato completo de consulta/gestión CRM. OpenAPI incluye ambas partes.

Este documento corresponde al código en `crm_service/api.py`, `schemas.py`,
`security.py` y `classification.py`, no a capacidades previstas. El inventario
de rutas, parámetros y esquemas de entrada se publica también en
[`openapi.json`](openapi.json), obtenido de `create_app(...).openapi()` y comprobado
por `tests/test_integration_contract.py`. El OpenAPI actual usa respuestas
`dict[str, Any]` en varias operaciones: los campos de salida que siguen se
verifican además mediante HTTP contra PostgreSQL desechable. No asumir que ese
OpenAPI por sí solo impone un esquema fuerte para todas las respuestas.

Para ejecutar los ejemplos con destinos ficticios y el procedimiento integral
verificado de dos empresas, seguir [`STAGING.md`](STAGING.md). Sus identificadores
`synthetic_one`, `synthetic_two` y `synthetic.employee.one` son datos de prueba;
`example_a`, `example_b` y `employee-007` en este documento son ejemplos genéricos
del contrato y no recursos productivos.

## Frontera de confianza

El navegador llama al **servidor** de `chatty-catalog-compass`; únicamente ese
servidor llama al CRM. Antes de reenviar, el proxy debe:

1. Validar su sesión y resolver en servidor la identidad individual y empresas
   autorizadas. No copiar `profile` ni `allowed_businesses` desde query, formulario,
   `X-Bot` o almacenamiento del navegador.
2. Emitir un JWT nuevo HS256 con `CRM_JWT_SECRET`, únicamente en servidor.
3. Construir una petición nueva con allowlist de cabeceras. **Eliminar `Origin` y
   `Sec-Fetch-Site`**, además de cualquier `Authorization` recibido del navegador;
   reemplazar este último por el JWT servidor. El CRM rechaza las dos primeras
   cabeceras con 403 aunque el token sea válido. No eliminarlas para exponer el
   backend al navegador: eliminarlas forma parte de un proxy autenticado servidor.
4. No enviar contraseñas de Lovable al CRM. No exponer JWT, secretos del CRM,
   credenciales de origen o claves privadas VAPID al navegador ni a logs.
5. Evitar caché compartida de respuestas entre empleados/empresas. Usar TLS/red
   privada y conservar la verificación de certificados.

`GET /health`, `GET /ready` y `GET /openapi.json` no requieren autenticación.
Todas las rutas `/v1/*` requieren `Authorization: Bearer <JWT>`. Las rutas
`/internal/*` requieren exclusivamente `X-CRM-Sync-Secret`; un JWT de frontend
no sustituye esa credencial operativa. El frontend de usuarios no debe exponer
estas rutas internas.

### JWT exacto

```json
{
  "iss": "chatty-catalog-compass",
  "aud": "whatsapp-crm",
  "profile": "employee-007",
  "allowed_businesses": ["example_a", "example_b"],
  "iat": 1793620800,
  "exp": 1793620920
}
```

El ejemplo es sintético y sus fechas fijas no deben reutilizarse como token de
prueba vigente. En cada emisión usar segundos Unix actuales para `iat` y `exp`.
El algoritmo aceptado es **solo HS256**. Firma, issuer, audience y expiración se
verifican mediante PyJWT. Los seis claims son obligatorios; `profile` debe ser un
string no vacío de hasta 200 caracteres; `allowed_businesses` una lista no vacía
de strings; `iat` y `exp` enteros, con `0 < exp - iat <= 300` segundos por defecto.
Recomendación del proxy: 120 segundos. Empresas desconocidas por la configuración
se descartan del alcance. Pedir expresamente una empresa fuera del alcance da 403.
La clave HMAC requiere al menos 32 caracteres y debe ser distinta de `CRM_SYNC_SECRET`.

Ejemplo de emisión **solo servidor**, con secreto de entorno y sin imprimirlo:

```python
import os
import time
import jwt

issued_at = int(time.time())
token = jwt.encode({
    "iss": "chatty-catalog-compass", "aud": "whatsapp-crm",
    "profile": "employee-007", "allowed_businesses": ["example_a", "example_b"],
    "iat": issued_at, "exp": issued_at + 120,
}, os.environ["CRM_JWT_SECRET"], algorithm="HS256")
# Usar token únicamente en Authorization de la llamada servidor → CRM.
```

`profile` es una identidad opaca confiada al servidor emisor, no una cuenta que
el CRM autentique por sí mismo. **Un perfil compartido `tanaka`, `memos` o `admin`
no representa a cada empleado**: leído, preferencias, suscripciones y auditoría
se comparten. La integración actual usa el acceso existente por perfil y permite
revocar la suscripción del dispositivo; no acredita identidad individual. Para
operación con identidad individual se necesita un identificador estable por persona
y revocación administrativa. No hay endpoints de alta/baja de empleados ni de
revocación global. El registro CRM de agentes refleja el último alcance de JWT
observado; Web Push no tiene un canal de revocación global de perfiles y el
vencimiento del JWT no elimina por sí mismo ese registro ni las suscripciones.
El frontend revoca la suscripción al salir/cambiar cuenta y el service worker debe
rechazar payloads que no correspondan a su vinculación activa `{profile,id}`.
El operador debe tener presente la limitación de cuentas compartidas; no hay
logout remoto de todos los dispositivos mediante este contrato.

## Convenciones de datos y errores

JSON UTF-8. Fechas RFC 3339/ISO 8601 con zona horaria; `null` significa ausencia
de valor. `business_id`, teléfonos e IDs de notas/tareas se codifican como
segmentos URL. Los teléfonos son strings y nunca claves globales: siempre van
acompañados de `business_id`. Todos los cuerpos Pydantic rechazan campos extra;
strings se recortan al validar. No enviar campos de estado del bot a un PATCH CRM.

Respuestas de error de negocio: `{"detail": "texto"}`. Validación de parámetros
o cuerpos: 422 con `{"detail": [{"loc": [...], "msg": "...", "type": "...", ...}]}`.
El consumidor no debe depender de textos traducidos exactos de errores.

| Código | Significado operativo |
| --- | --- |
| 400 | Cursor inválido, vencido o correspondiente a otros filtros/perfil/alcance |
| 401 | Token/credencial interna ausente o inválida; JWT vencido/claim incorrecto; las rutas internas también rechazan cabeceras de navegador con este código |
| 403 | Cabeceras de navegador en `/v1/*` o empresa fuera del alcance autenticado |
| 404 | Conversación/nota/tarea/suscripción/solicitud no accesible o inexistente |
| 409 | Versión CRM desactualizada o endpoint push perteneciente a otro perfil |
| 422 | Cuerpo, parámetros, asignación o endpoint push no válidos |
| 503 | Base CRM indisponible o migraciones requeridas en `/ready` |

Los límites de página son 1–200. En listas con `offset`, el mínimo es cero.
No existe un total global de resultados en las respuestas paginadas.

## Tipos de respuesta

### Resumen `Conversation`

| Campo | Tipo / semántica |
| --- | --- |
| `business_id`, `phone_number`, `name` | strings; nombre puede estar vacío |
| `bucket` | `awaiting_human`, `ai_active` o `closed`; `human_active` es un alias de consulta legado |
| `attention_since` | fecha o null; primer mensaje pendiente o inicio inferido de atención |
| `waiting_seconds` | entero no negativo, calculado por el backend al responder |
| `unanswered_customer_count` | entero; mensajes del ciclo actual sin respuesta humana identificada |
| `priority` | entero 1–4 para atención, 0 para otros buckets |
| `priority_reason` | string explicativo del clasificador central |
| `last_incoming_at` | fecha o null |
| `last_message_id` | entero del último ID importado para ese contacto/empresa |
| `data_quality_warnings` | lista de strings, incluidas inferencias legacy si aplican |
| `stage` | string CRM libre, inicialmente `new`; no representa estado del bot |
| `tags` | lista de strings CRM |
| `assigned_to` | perfil de agente o null |
| `version` | entero para control optimista del PATCH CRM |
| `read` | booleano por perfil, empresa y conversación; nunca cambia el bucket |
| `last_activity_at` | fecha UTC de la última actividad conversacional; no cambia por leído o recibos técnicos |
| `chatwoot_url` | deep link calculado o null; no significa que el CRM haya contactado Chatwoot |

Ejemplo sintético abreviado de una página (el objeto `Conversation` completo
siempre contiene los campos anteriores):

```json
{
  "items": [{
    "business_id": "example_a", "phone_number": "contact-001", "name": "Persona ficticia",
    "bucket": "awaiting_human", "attention_since": "2026-11-02T12:00:00+00:00",
    "waiting_seconds": 120, "unanswered_customer_count": 1, "priority": 2,
    "priority_reason": "paused_without_chatwoot", "last_incoming_at": "2026-11-02T12:00:00+00:00",
    "last_message_id": 7, "data_quality_warnings": [], "stage": "new", "tags": [],
    "assigned_to": null, "version": 1, "read": false, "chatwoot_url": null
  }],
  "next_cursor": null
}
```

### Detalle, mensaje, nota y tarea

`ConversationDetail` añade a `Conversation`:

- `source_state`, `customer`, `handoff_delivery`: objetos JSON importados del
  origen, que pueden estar vacíos o carecer de campos en despliegues legacy.
- `messages`: lista de `Message` para la página solicitada.
- `next_cursor`: string opaco o null para obtener mensajes **más antiguos**.

`Message` contiene `id: integer`, `role: string`, `content: string`,
`created_at: fecha`, `metadata: object`, `event_key: string|null`. No asumir que
todos los registros legacy tienen metadata moderna o event_key.

`Note` contiene `id: string`, `body: string`, `profile: string`, `updated_at: fecha`.
`Task` contiene `id: string`, `title: string`, `profile: string`,
`assigned_to: string|null`, `due_at: fecha|null`, `completed: boolean`.
`profile` identifica al creador; no se acepta como campo del cuerpo.

## Endpoints y parámetros reales

En las tablas, `C` es literalmente
`/v1/conversations/{business_id}/{phone_number}`. Ambos parámetros de ruta son
strings obligatorios. `{item_id}` es el string devuelto al crear el recurso.

### Salud, capacidades y ámbito

| Método y ruta | Entrada | Respuesta |
| --- | --- | --- |
| `GET /health` | ninguna | 200 `{"status":"ok","service":"whatsapp-crm"}` |
| `GET /ready` | ninguna | 200 `{"status":"ready"}`; 503 si PostgreSQL/migración CRM no están disponibles; no verifica fuentes ni baseline |
| `GET /openapi.json` | ninguna | 200 documento OpenAPI; no sustituye las reglas de JWT imperativas de `security.py` |
| `GET /v1/capabilities` | JWT | 200 objeto de flags descrito a continuación |
| `GET /v1/businesses` | JWT | 200 `[{"business_id":"example_a"}]`, solo empresas autorizadas/configuradas |
| `GET /v1/agents` | JWT | 200 `[{"profile":"employee-007","businesses":["example_a"]}]`, perfiles registrados que comparten alguna empresa accesible; la lista de empresas de cada perfil se intersecta con el alcance del actor |

Capabilities anuncia métodos de fuente limitados a lectura, no una atestación
de privilegios Supabase: `source_methods_read_only=true` y
`source_credentials_verified=false`. `source_read_only` y
`production_sources_enabled` son verdaderos con modo bot_api y fuentes habilitadas;
no certifican que un bot remoto ya esté actualizado. `source_access_mode` indica
el modo configurado y `sync_execution="worker"`. `crm_updates=true`;
`human_control_businesses` enumera las empresas autorizadas con control habilitado.
`manual_handoff_enabled`, `send_messages`, `resolve_conversations` y `resume_bot`
son verdaderos si esa lista no está vacía; comprobar la pertenencia por negocio.
`manual_handoff_contract={"version":1,"transport":"authenticated_bot_endpoint_only","implemented":true}`;
`send_transport="authenticated_bot_endpoint_only"`. La API no envía a Meta.
`notifications_enabled` refleja la configuración, que en staging permanece falsa.

### Inbox y detalle

`GET /v1/inbox` devuelve 200 `{"items": [Conversation], "next_cursor": string|null}`.

| Query | Tipo / valor predeterminado |
| --- | --- |
| `bucket` | enum de buckets o `all`; predeterminado `awaiting_human` |
| `business_id` | lista opcional mediante query repetida: `?business_id=example_a&business_id=example_b`; si falta, todas las autorizadas; una empresa no autorizada rechaza la petición completa |
| `search` | string de hasta 300 caracteres, predeterminado vacío; busca nombre, teléfono o contenido importado |
| `priority` | entero opcional entre 0 y 4 |
| `assigned_to` | string opcional; `unassigned` filtra asignación null |
| `tag`, `stage` | strings opcionales, coincidencia exacta |
| `cursor` | string opaco opcional de la respuesta anterior |
| `limit` | entero 1–200; predeterminado 50 |

En `awaiting_human` el backend ordena por prioridad y espera; en `all`, IA y
cerradas ordena por `last_activity_at` descendente (actividad conversacional,
no leído, consulta de control ni recibos técnicos). No recalcular
el bucket en el frontend a partir de eventos históricos. El cursor dura 15
minutos y queda vinculado al perfil, alcance y filtros. Conservar los mismos
filtros al solicitar la siguiente página. No proporciona snapshot entre llamadas;
si una sync cambia clasificación durante la navegación, refrescar desde la primera
página. El resultado no incluye `total` ni `has_more`.
Para la cola humana, consultar `bucket=awaiting_human`: incluye conversaciones
pausadas hasta cierre/devolución a IA, aunque el asesor ya haya contestado.
`priority_reason=human_waiting_customer`, cero mensajes pendientes y
`attention_since=null` distinguen la espera del cliente. Ya no existe una
categoría visible «En gestión». Eventos de sistema posteriores al cierre no
reabren la conversación; un nuevo mensaje del cliente sí.

El worker prioriza mensajes incrementales y consulta únicamente el contacto y
estado de los teléfonos afectados, mediante GET del exportador autenticado.
La reconciliación completa sigue siendo acotada y reanudable. El exportador
anuncia `X-CRM-Phone-Filter: 1`; versiones anteriores conservan el camino de
reconciliación, sin confiar en un filtro ignorado. `SYNC_INTERVAL_SECONDS`
predetermina 5 segundos; una configuración explícita anterior se respeta.

`GET C` admite `limit` 1–200 (predeterminado 100) y `cursor` opcional. Devuelve
200 `ConversationDetail`. La primera página contiene los mensajes más recientes,
pero los presenta en orden cronológico dentro de la página; `next_cursor` obtiene
la página más antigua. Codificar el teléfono como segmento de URL, no convertirlo
en número ni usarlo sin la empresa.

### Estado CRM, leído y CRUD por conversación

| Método y ruta | Cuerpo / query | Respuesta |
| --- | --- | --- |
| `PATCH C/crm` | `{"version":1,"stage":"qualified","assigned_to":"employee-007","tags":["synthetic"]}`; solo `version` es obligatorio | 200 `Conversation` con versión incrementada; 409 ante versión desactualizada |
| `POST C/read` | objeto obligatorio; `{}` equivale a `{"read":true}`; `{"read":false}` marca no leído | 200 `{"read":true}` o `false`; no modifica clasificación |
| `GET C/notes` | `offset=0`, `limit=50` (máximo 200) | 200 `[Note]`, orden por actualización descendente e ID |
| `POST C/notes` | `{"body":"Nota de prueba"}` | 201 `Note` |
| `GET C/notes/{item_id}` | sin cuerpo | 200 `Note` |
| `PATCH C/notes/{item_id}` | `{"body":"Nota editada"}` | 200 `Note` |
| `DELETE C/notes/{item_id}` | sin cuerpo | 204, sin JSON |
| `GET C/tasks` | `offset=0`, `limit=50` (máximo 200) | 200 `[Task]`, orden por ID |
| `POST C/tasks` | `{"title":"Llamar","assigned_to":null,"due_at":"2030-01-01T10:00:00Z","completed":false}` | 201 `Task` |
| `GET C/tasks/{item_id}` | sin cuerpo | 200 `Task` |
| `PATCH C/tasks/{item_id}` | cualquiera de `title`, `assigned_to`, `due_at`, `completed`; solo se actualizan campos presentes | 200 `Task` |
| `DELETE C/tasks/{item_id}` | sin cuerpo | 204, sin JSON |

Límites de entrada: `stage` 1–80 caracteres; `assigned_to` hasta 200 o null;
`tags` máximo 30 elementos no vacíos de hasta 80 caracteres, recortados,
deduplicados y ordenados. `stage`/`tags` no aceptan null al actualizar; enviar
`assigned_to:null` desasigna. El perfil asignado debe estar registrado con acceso
a esa empresa; su primer JWT válido crea/actualiza el registro, no existe POST
de alta de agente. `version` entero mínimo 1. Un PATCH con solo `version`
también incrementa la versión.

`NoteInput.body`: 1–20.000 caracteres. `TaskInput.title`: 1–1.000, obligatorio
al crear. `due_at` es opcional/null, pero si se envía una fecha debe incluir zona
horaria. Al crear, `completed` predeterminado false y los otros campos null.
En PATCH de tarea, `title` y `completed` no aceptan null; `due_at` y `assigned_to`
sí, para limpiar esos valores. Notas y tareas son compartidas dentro de la
empresa autorizada; el API no restringe edición exclusivamente al autor.

### Pipeline, analytics, preferencias y notificaciones

| Método y ruta | Entrada | Respuesta |
| --- | --- | --- |
| `GET /v1/pipeline` | JWT; sin filtros propios | 200 `{"items":[{"business_id":"example_a","stage":"new","count":1}]}` |
| `GET /v1/analytics` | JWT; sin filtros propios | 200 `{"snapshot_at":"fecha","items":[{"business_id":"example_a","bucket":"awaiting_human","count":1}]}` |
| `GET /v1/notifications` | `offset=0`, `limit=50` (1–200) | 200 `{"items":[{"id":"...","business_id":"example_a","phone_number":"contact-001","url":"/chats?...","created_at":"fecha"}]}` |
| `GET /v1/me/preferences` | JWT | 200 objeto indexado por empresas autorizadas, por ejemplo `{"example_a":{"in_app":true,"web_push":true}}` |
| `PATCH /v1/me/preferences` | `{"business_id":"example_a","in_app":false,"web_push":false}`; empresa obligatoria, ambos booleanos predeterminados true | 200 `{"ok":true}` |
| `GET /v1/push/config` | JWT del perfil actual | 200 `{"enabled":true,"configured":true,"public_key":"base64url-P256","reason":null,"allowed_businesses":["example_a"]}`; apagado/sin claves devuelve `configured:false`, clave null y motivo |
| `POST /v1/push/subscriptions` | `{"endpoint":"https://proveedor-permitido/...","keys":{"p256dh":"base64url","auth":"base64url"}}` | 201 `{"id":"..."}`; repetir endpoint del mismo perfil conserva ID; 409 si corresponde a otro perfil |
| `DELETE /v1/push/subscriptions/{item_id}` | JWT, ID propio | 204; desactiva la suscripción |

Pipeline y analytics son **conteos actuales**, no métricas históricas ni tiempos
de respuesta. Notificaciones se filtran por alcance y preferencias in-app; no
hay endpoint para marcar una notificación leída. El deep link usa empresa,
teléfono y `bucket=awaiting_human`. Las preferencias no activan por sí solas el
flag global: staging mantiene notificaciones desactivadas.

La suscripción admite endpoint de hasta 4096 caracteres y claves base64url
`p256dh` de 80–200 y `auth` de 20–100. Solo permite HTTPS/443 y proveedores de
la lista fija `Settings.push_hosts` (FCM, Mozilla, Apple y Windows). No hay una
variable `PUSH_ALLOWED_HOSTS` implementada. El formato aceptado no implica un envío push ya verificado.
`GET /v1/push/config` deriva la clave pública desde `VAPID_PRIVATE_KEY`; esa clave
privada no sale del servidor. Los motivos de falta de disponibilidad son
`disabled`, `vapid_missing`, `vapid_invalid` y `vapid_subject_missing`. La respuesta
conserva HTTP 200 para que la UI pueda explicar la configuración pendiente;
sin JWT válido responde 401. El payload Web Push contiene `id`, `tag`, `title`,
`url`, `profile` y `subscription_id`, sin contenido del mensaje ni credenciales.
El service worker debe comparar perfil/suscripción con su vinculación activa
antes de mostrarlo y sanear el enlace a una ruta local de chats.

### Sincronización operativa, sin secretos de fuente en API

`GET /v1/sync-status` devuelve `{"items":[...]}` solo para las empresas permitidas.
Cada objeto contiene `business_id`, `baseline_complete`, `last_success` (fecha o
null), `last_error` (código o null), `data_quality_warnings` (lista), `stale`
(booleano), y el estado/métricas de presupuesto de la ejecución: `run_state`
(`pending`, `ok`, `partial` o `failed`), `next_retry_at` (fecha o null) y
`metrics={pages,requests,rows,duration_seconds,read_seconds,connections_peak}`.
`metrics` está vacío antes de una ejecución; páginas, peticiones, filas y pico
de conexiones son contadores enteros, y las duraciones están en segundos.
`stale` utiliza el último éxito y no reemplaza comprobar baseline completo.

`POST /internal/sync` **encola** trabajo en la base CRM y responde 202. No abre
conexiones a orígenes ni necesita sus credenciales en `crm-api`:

```http
POST /internal/sync
X-CRM-Sync-Secret: <secreto operativo del CRM, distinto del JWT>
Content-Type: application/json

{"business_id":"example_a","idempotency_key":"synthetic-backfill-001"}
```

Respuesta:

```json
{
  "id":"uuid-opaco", "business_id":"example_a", "status":"pending",
  "created_at":"2026-11-02T12:00:00+00:00", "updated_at":"2026-11-02T12:00:00+00:00",
  "result":{}
}
```

La pareja empresa/idempotency_key identifica una solicitud: repetirla devuelve
el mismo ID, no ejecuta otra sync inline. `idempotency_key` requiere 1–100
caracteres. `GET /internal/sync/{request_id}` usa el mismo secreto y devuelve 200
con el objeto de solicitud, o 404. `pending` significa encolada, **no** backfill
completo. El worker consume la solicitud con su configuración y límites; las
credenciales de origen pertenecen exclusivamente al worker. El frontend de
usuarios no debe recibir `CRM_SYNC_SECRET` ni poder actuar como operador interno.

`status` de una solicitud es `pending`, `done` o `failed`. `result` es un objeto
empresa→resultado (`ok`, `partial`, `failed`, `backoff`, `already_running` o
`business_removed` cuando una empresa pendiente fue retirada del registro). Un
paso parcial/backoff conserva la solicitud pendiente para el worker; `done` solo
indica que esa ejecución terminó. El estado de baseline y antigüedad se comprueba
en `/v1/sync-status`. Repetir una clave de idempotencia que terminó fallida no
crea un intento nuevo; una intervención posterior debe usar otra clave.

## Funciones que todavía NO tienen endpoint o implementación

- Listado global de tareas entre conversaciones, filtros globales por vencimiento
  o un calendario CRM; solo existen tareas bajo `C/tasks`.
- Métricas de tiempos de respuesta humana, percentiles, SLA, cohortes históricas
  o embudos de conversión. `waiting_seconds` por conversación no implementa eso.
- Directorio/autenticación de empleados, invitaciones, alta/baja, roles de empleado,
  logout remoto y revocación de suscripciones por administrador. `/v1/agents` solo
  lista perfiles previamente observados.
- Adjuntos humanos, plantillas fuera de la ventana de WhatsApp, cambio forzado de
  propietario o recuperación automática de un envío de resultado incierto.
  El CRM no tiene emisor Meta ni permite escritura directa a tablas del origen.
- Catálogo editable de etapas/etiquetas, eliminación de conversaciones, endpoints
  de auditoría, marcar notificaciones como leídas o búsqueda/paginación global de mensajes.
- Garantía de entrega única de Web Push ante crash después de aceptación del
  proveedor y antes de commit. La implementación conserva ID/tag para deduplicar
  en el service worker; no promete entrega exactamente una vez del proveedor.

No habilitar pantallas/acciones a partir de esta lista de trabajo futuro. Para el
primer piloto seguir la guía Briosos: secreto de exportación GET sin credenciales
administrativas, control por otro secreto, identidad individual, aprobación del
proxy y prueba aislada. Sintético sigue disponible y NOTIFICATIONS_ENABLED=false.

### Missing command receipt recovery

A typed bot `404 {"detail":"command_not_found"}` makes CRM query the current authoritative control, never repeat the original mutation. An outstanding take can become `confirmed` only if that bot currently confirms a paused human conversation owned by the ledger actor; the result has `reconciled_from_control=true`. This confirms the control state, not a provider message. It does not bypass the WhatsApp window (`can_send` may still be false). Message outcomes remain unknown without provider evidence, with `error=command_result_missing_no_resend` and current control when available. Generic 404s do not establish command absence. Existing cached acceptance/delivery evidence is preserved. No SQL, migration, new configuration or infrastructure changes.
