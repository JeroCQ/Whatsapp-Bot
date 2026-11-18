# Chatty · Configuración central, turno 1

**Not executed or runtime-validated. Do not deploy yet.**

## Bases y publicación

- Backend: `origin/feat/crm-media-delivery@582ac58ee1e202b59c86fc4a0ae4645f31d0f88f`.
- Frontend: `origin/main@43678888e99713ba6640b60cd09a2da5a70e326d`.
- Ambas ramas de trabajo: `feat/chatty-central-config-20261118`.
- Commit backend activo **informado por el usuario**: `f2b30f1b`. No se equipara
  al HEAD de Git ni se confirmó contra Railway. Los tres servicios staging están
  en el entorno production según el encargo: no se consideran aislados por su nombre.
- Se inspeccionaron los bindings documentados: ramas legacy de bots, staging y
  `feat/crm-media-delivery`; Lovable usa `main`. No hay conexión Railway/token
  configurados para confirmar metadata actual. No se instalaron herramientas.
  El workflow del backend es `workflow_dispatch` (recuperación manual de catálogo),
  sin activación por push; el frontend no tiene workflows de despliegue en el repo.
  No se cambiaron bindings, workflows ni fuentes desplegadas.

## Almacenamiento preparado · NOT APPLIED

Archivo exacto: `crm/alembic/versions/0005_central_config_usage.py`.
Continúa `0004_delivery_receipts`, usando **el PostgreSQL central CRM existente**
identificado por el `CRM_DATABASE_URL` de su API/worker. No es el Supabase de
Tanaka, Memo’s, Velvet o Briosos. No hay SQL adicional para bots ni seeds.

Solo crea: crm_business_drafts, crm_shared_config_draft,
crm_business_connection_refs, crm_usage_turns, crm_model_usage_attempts y
crm_support_minutes. Reutiliza crm_agents y audit_events. No modifica las tablas
de conversaciones, sincronización, asignación, medios o control humano.

No ejecutar la migración en este turno. **No desplegar esta rama:** el Railway
config existente del API tiene `preDeployCommand = "alembic upgrade head"` y un
despliegue podría aplicar la revisión. No se añadió lógica de schema al arranque.
El downgrade elimina únicamente las tablas nuevas y perdería sus registros;
requiere una decisión explícita y respaldo en una fase posterior.

## Configuración en borrador

Settings.businesses sigue siendo la autoridad actual. La lista Admin une sus IDs
con borradores nuevos sin crear SourceConversation, clientes ni permisos nuevos.
Se guardan nombre e IDs WhatsApp de canal (Phone Number ID y Business Account ID),
no tokens. La base impide estados distintos de `draft` y que dos borradores
compartan Phone Number ID. Guardar no activa nada.

`central_config.preview(session, settings, business_id)` superpone defaults legacy,
borrador compartido y overrides por negocio. Devuelve valores/orígenes compatibles
con los nombres y límites de Settings, pero no construye ni cambia Settings.
Solo admite timezone, locale, sync_interval, page_size, max_pages,
sync_max_seconds, notifications_enabled y operations_enabled. `{}` hereda; null
explícito, claves desconocidas, URLs y credenciales no son ajustes válidos.

Las conexiones son referencias backend `legacy_settings:<business>:<purpose>` a
la configuración ya existente; no contienen ni resuelven valores secretos. El
browser solo recibe propósito/presencia observable en **el API**, no referencia,
URL o secreto. Esto no verifica conectividad ni la configuración del worker.
No hay env vars nuevas por negocio ni selector de credenciales desde el navegador.

Membresías, alias Tanaka/Velvet, rol y cola Memo’s se leen de crm_agents existentes;
no se duplican ni editan desde el borrador. Admin compartido sigue siendo un actor
técnico, no acreditación de una identidad individual.

## API sobre autenticación actual

Todos estos endpoints requieren el JWT servidor HS256 actual y rol Admin
resuelto en servidor. La llamada directa del navegador sigue prohibida. La
allowlist `/api/crm/admin/*` solo expone lectura de uso, borradores y soporte;
**no** expone ingesta de telemetría ni activación. Los perfiles de negocio reciben
403 tanto en proxy como en backend. No cambia el alcance de ningún endpoint viejo.

- `GET /v1/admin/config`: lista de negocios, defaults y estado de recepción de uso.
- `PATCH /v1/admin/config/defaults`: `{version:0,values:{}}`. Versión 0 crea el
  primer borrador; después se exige la versión vigente. values reemplaza el
  mapa compartido, no aplica cambios al runtime.
- `GET /v1/admin/config/businesses/{business_id}`: borrador, preview y membresías.
- `PATCH /v1/admin/config/businesses/{business_id}`: `{version:0,display_name:
  "Negocio ficticio",whatsapp_phone_number_id:null,whatsapp_business_account_id:null,
  overrides:{}}`. Crea/actualiza únicamente ese borrador. 409 exige recargar;
  no hay sobrescritura silenciosa, merge automático ni activación.
- `GET /v1/admin/usage?business_id=...&limit=100&cursor=...&format=json|csv`:
  intentos, correlaciones, tokens disponibles, latencia/outcome y conteos separados
  de intentos/turnos/respuestas aceptadas. Cursor firmado y orden estable. CSV
  exporta **una página**, máximo 1000 intentos; campos desconocidos vacíos y
  cost_status=unavailable, con celdas peligrosas de spreadsheet neutralizadas.
- `POST /v1/admin/usage/attempts`: solo servidor, desactivado por defecto. Body:
  business_id, conversation_id, turn_id, attempt_id, attempt_index, purpose,
  model_name, model_version opcional, observed_at con timezone, outcome
  success/failed/timeout/cancelled/unknown; input_tokens/output_tokens/total_tokens/
  cached_input_tokens/thought_tokens/latency_ms opcionales. No texto de modelo.
  business+attempt_id es idempotente; cambiar su contenido produce 409. Se crea
  correlación de turno desconocido si falta, sin crear conversación/cliente.
- `POST /v1/admin/usage/turns`: solo servidor, desactivado. business_id,
  conversation_id, turn_id, started_at opcional y outcome
  unknown/reply_accepted/no_reply/failed. Un intento exitoso **no** marca respuesta;
  solo una confirmación explícita futura puede contar reply_accepted (no delivered).
- `GET /v1/admin/support-minutes?business_id=...`: hasta 100 registros recientes.
- `POST /v1/admin/support-minutes`: `{id:"<uuid-estable>",business_id:"<id>",
  minutes:15,occurred_at:"2026-11-18T10:00:00-05:00",category:"incident",note:""}`.
  Categorías setup/incident/maintenance/other. Autor fijado por el JWT; mismo UUID
  y payload se confirman sin duplicar. No cambia asignaciones ni crea tickets.

Sin migración, los nuevos endpoints devuelven 503; los endpoints operativos siguen
su comportamiento actual. Guardar borradores y soporte se audita sin copiar sus
valores/nota ni secretos a logs. Nuevos negocios en borrador no adquieren acceso
operativo ni comienzan a sincronizar.

## Colector preparado, no conectado

`chatty_usage.py` usa solo stdlib. `UsageCollector()` está apagado y no inicia
threads, llamadas HTTP, schema ni procesos. Al habilitarlo explícitamente en un
piloto futuro y proporcionar un sink del servidor, la cola en memoria es acotada;
el productor no espera al sink. Se registran errores de telemetría con códigos
fijos, sin contenidos, tokens de acceso ni exception text. No hay reintentos de
modelo ni de envío. Un sink lento/caído no falla la respuesta del cliente.

La futura instrumentación debe envolver **cada llamada individual** al proveedor
dentro del mecanismo de retries existente (no el turno completo). Usar
`attempt(...)/async_attempt(...)`, llamar `observation.observe_response(response)`
y emitir `record_turn(...)` por separado desde la autoridad real de respuesta.
Metadata SDK ausente queda null; los fallos observables incluyen tipo de error,
outcome y latencia sin inventar tokens consumidos. No se inspecciona prompt ni
texto de salida. No se encontraron tarifas de modelos en el código pertinente;
coste permanece desconocido, sin motor de billing ni estimaciones.

El sink futuro deberá usar el canal autenticado servidor existente con el mínimo
alcance del piloto, timeout acotado y payload inmutable. La cola local no es un
registro durable: eventos sin aceptar por el API pueden perderse en un reinicio
o desbordamiento; no se presentan como consumo cero. El almacenamiento central
sí persiste los eventos aceptados y deduplica sus IDs. No se añade infraestructura
ni se promete cobertura de consumo total antes de conectar/probar el piloto.

Única opción global nueva preparada: **`CRM_USAGE_COLLECTION_ENABLED=false`**
(default false en Settings del CRM). Mantenerla ausente/false ahora. Solo abre la
ingesta futura; no instrumenta bots ni activa configuración central. No hace falta
crear secretos/variables nuevas en esta entrega.

## Administración y continuidad

Admin → Gestión Bot → **Negocios · Borrador** usa el índice existente `/dashboard`.
Las pestañas Instrucción/Historial/Catálogo y la navegación no-Admin se conservan.
La UI tiene editor de drafts compartidos/por negocio, preview no aplicado,
membresías de solo lectura, tabla/exportación de uso y formulario de minutos.
No hay botones para activar, enviar WhatsApp, tomar control o desplegar.

No se ejecutaron pruebas ni se escribieron tests; no se ejecutaron app, lint,
typechecks, builds, navegador, Docker, migraciones o proveedores. No cambió
enrutamiento, configuración real, datos, secretos, catálogo, reset, media,
seguimientos, propiedad, pausas o sincronización. Solo revisión estática de diff.

El turno 2 debe continuar **en `feat/chatty-central-config-20261118` de ambos
repositorios**, preservando estos commits; no empezar otra integración ni activar
servicios desde esta entrega. Cualquier piloto, migración o despliegue requiere
su alcance autorizado posterior, especialmente al compartir el entorno production.
