# CRM independiente para WhatsApp

Backend ubicado exclusivamente en `crm/`, en la rama `43-CRM`. No importa módulos
del bot, no usa Redis y no cambia su runtime, webhooks, migraciones o despliegues.
El frontend se mantiene como referencia de solo lectura en
`chatty-catalog-compass@1cda4a8069d189834837e082b378ae05629a1f02`.

## Arquitectura

```mermaid
flowchart LR
  S[Supabase aislado por empresa] -->|GET / HEAD| W[sync-worker]
  W --> D[(PostgreSQL exclusivo CRM)]
  W -->|outbox / pywebpush| P[Proveedor Web Push]
  B[Navegador] --> F[Servidor chatty-catalog-compass]
  F -->|JWT HS256 de vida corta| A[api FastAPI]
  A --> D
  A -->|deep link| C[Chatwoot existente]
```

La flecha desde Supabase representa **lectura**: el worker solicita páginas
PostgREST. Ningún componente del CRM puede modificar esos orígenes mediante su
adaptador. Una caída del CRM no participa en el camino de recepción o respuesta
del bot. Cada negocio tiene conexiones, cursores y bloqueo PostgreSQL propios.

## Desarrollo local

Requisitos: Python **3.12** y PostgreSQL 15 o posterior. Crear una base NUEVA llamada
`crm` y una base desechable `crm_test`, con un usuario dedicado. No usar una base
del bot ni ejecutar los SQL del directorio `supabase/` del repositorio padre.

Desde este directorio:

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
cp .env.example .env
# Completar .env localmente; no pegar secretos en chat ni versionarlos.
# Generar dos secretos distintos con: python -c 'import secrets; print(secrets.token_urlsafe(48))'
set -a
. ./.env
set +a
.venv/bin/alembic upgrade head
.venv/bin/uvicorn crm_service.api:create_app --factory --host 127.0.0.1 --port 8080 --no-access-log
```

En otra terminal, exportar las mismas variables e iniciar:

```bash
.venv/bin/python -m crm_service.worker
```

`GET /health` comprueba el proceso. `GET /ready` comprueba PostgreSQL y la revisión
Alembic; **no afirma que los orígenes estén sincronizados**. Ver `/v1/sync-status`
para baseline, última sincronización, errores y antigüedad. No se carga `.env`
automáticamente. Se permite iniciar sin credenciales de origen para desarrollar
la API; el worker informa `source_configuration_missing` por empresa.

### Configuración

| Variable | Uso |
| --- | --- |
| `CRM_DATABASE_URL` | Base exclusiva CRM; esquema `postgresql+psycopg://` |
| `CRM_JWT_SECRET` | Secreto HS256 compartido solamente con el servidor del frontend, mínimo 32 caracteres |
| `CRM_SYNC_SECRET` | Secreto interno diferente, mínimo 32 caracteres |
| `CRM_BUSINESSES` | Registro extensible; por defecto `tanaka,memos,velvet` |
| `<BUSINESS>_SOURCE_SUPABASE_URL` | Origen HTTPS aislado; no se permite compartir el mismo origen entre empresas |
| `<BUSINESS>_SOURCE_SUPABASE_SERVICE_ROLE_KEY` | Credencial de lectura vía PostgREST; nunca se almacena en las tablas CRM |
| `<BUSINESS>_CHATWOOT_APP_URL` | Origen HTTPS sin `/app` ni ruta adicional |
| `<BUSINESS>_CHATWOOT_ACCOUNT_ID` | Cuenta numérica para construir el deep link |
| `NOTIFICATIONS_ENABLED` | `false` por defecto; habilita nuevos eventos y entregas |
| `VAPID_PRIVATE_KEY`, `VAPID_SUBJECT` | Clave privada VAPID y contacto `mailto:` para Web Push |
| `PUSH_ALLOWED_HOSTS` | Proveedores push permitidos, separados por coma; HTTPS/443 únicamente |
| `SYNC_INTERVAL_SECONDS` | Intervalo mínimo 5 s, predeterminado 30 s |

El prefijo de empresa es su identificador en mayúsculas. Un negocio futuro solo
requiere registro y sus propias variables. No hay ramas de código por marca.
Chatwoot es exclusivamente un enlace: no se usan sus credenciales ni se llama su API.

### Clasificación

`crm_service/classification.py` es la única implementación. Identifica el último
`RESOLVED:` y descarta handoffs y respuestas humanos de ciclos anteriores.
Con el bot pausado, cualquier cliente sin respuesta, ausencia de respuesta humana,
handoff pendiente/incierto o ausencia de conversación Chatwoot exige atención.
Las referencias de metadata tienen precedencia sobre el orden temporal; sin
referencias se usa `(created_at, id)` y se expone una advertencia legacy.
El bot reutiliza filas de handoff sin reiniciar `created_at`: si `updated_at`
pertenece al ciclo nuevo, se considera el handoff actual con advertencia explícita.
La ausencia de `is_paused` degrada conservadoramente a atención, nunca a cierre.

`attention_since` es el primer mensaje sin respuesta o, si no hay uno, el inicio
inferible del handoff. Si no hay fecha fiable, es `null` con advertencia y
`waiting_seconds=0`. La API recalcula los segundos desde esa fecha almacenada;
el frontend no recalcula el bucket. Leído/no leído se guarda por perfil y empresa,
y nunca altera el bucket.

El orden de atención usa prioridad 1 (handoff incierto), 2 (sin Chatwoot),
3 (creación pendiente), 4 (resto), espera más antigua y entrada más reciente.
La paginación es por clave compuesta y cursor firmado vinculado al actor, alcance
y filtros. Si cambia la clasificación durante la navegación, refrescar la primera
página; no se promete un snapshot entre peticiones.

## API e integración con el servidor frontend

Todas las rutas `/v1/*` requieren `Authorization: Bearer <JWT>` firmado con HS256:

```json
{
  "iss": "chatty-catalog-compass",
  "aud": "whatsapp-crm",
  "profile": "identificador-estable-del-actor",
  "allowed_businesses": ["tanaka"],
  "iat": 1793620800,
  "exp": 1793620920
}
```

Vida máxima: 300 segundos; recomendada: 120. `iat` y `exp` son enteros Unix.
El servidor frontend debe resolver el perfil y empresas con su autenticación
existente **antes** de firmar; no copiar `allowed_businesses` desde el navegador.
El CRM no recibe ni guarda las contraseñas de Lovable. El perfil debe ser único
por actor para obtener leído/no leído y preferencias individuales; si el frontend
firma un perfil compartido, esos estados serán compartidos por definición.

La API no tiene CORS y rechaza llamadas con `Origin`/`Sec-Fetch-Site`. El JWT y
`CRM_JWT_SECRET` nunca deben llegar al navegador. En Railway, utilizar red privada
para el backend cuando el servidor frontend comparta la red; si no es posible,
usar HTTPS con autenticación server-to-server y limitar ingreso en la infraestructura.
Las respuestas CRM deben servirse desde el frontend sin caché compartida entre perfiles.

| Método / ruta | Contrato |
| --- | --- |
| `GET /health`, `GET /ready` | Proceso / base CRM y migración |
| `GET /v1/capabilities` | Capacidades disponibles, handoff manual siempre deshabilitado |
| `GET /v1/businesses`, `GET /v1/agents` | Empresas y agentes accesibles al actor |
| `GET /v1/inbox` | `bucket`, `business_id` repetible, `search`, `priority`, `assigned_to`, `tag`, `stage`, `cursor`, `limit` |
| `GET /v1/conversations/{business_id}/{phone_number}` | Datos, clasificación y mensajes paginados con `cursor`/`limit` |
| `PATCH /v1/conversations/{business_id}/{phone_number}/crm` | `version` requerido; etapa, asignación y etiquetas; 409 si cambió la versión |
| `GET/POST .../notes`, `GET/PATCH/DELETE .../notes/{id}` | Notas CRM; cuerpo `{ "body": "..." }` |
| `GET/POST .../tasks`, `GET/PATCH/DELETE .../tasks/{id}` | Tareas; título, `due_at` con zona horaria, asignado y completado |
| `POST .../read` | `{ "read": true }` o `false`, por actor |
| `GET /v1/pipeline`, `GET /v1/analytics` | Conteos actuales por empresa/etapa o bucket; no métricas históricas de SLA |
| `GET /v1/notifications` | Historial in-app sujeto al alcance y preferencias |
| `GET/PATCH /v1/me/preferences` | Preferencias; PATCH `{ "business_id": "tanaka", "in_app": true, "web_push": false }` |
| `POST /v1/push/subscriptions` | `{ "endpoint": "https://...", "keys": { "p256dh": "...", "auth": "..." } }` |
| `DELETE /v1/push/subscriptions/{id}` | Desactiva exclusivamente la suscripción propia |
| `GET /v1/sync-status` | Salud y advertencias por empresa autorizada |
| `POST /internal/sync` | Sync idempotente; requiere `X-CRM-Sync-Secret`, no acepta JWT de frontend como sustituto |

En la tabla, `...` significa `/v1/conversations/{business_id}/{phone_number}`.
Notas, tareas y notificaciones admiten `offset` y `limit` (máximo 200). El inbox
admite `assigned_to=unassigned`. Las asignaciones requieren un agente ya registrado
con acceso al negocio; el registro se crea al recibir su primer JWT válido.
La API documentada también está disponible como `/openapi.json`, sin datos operativos.

El contrato futuro de handoff manual se publica como capability y no implementa
ninguna acción. Una implementación posterior tendrá que usar el endpoint
autenticado del bot. No hay composer, Meta, reanudación ni resolución desde el CRM.

### Notificaciones

El primer backfill de cada empresa establece baseline sin eventos. Después,
únicamente una transición nueva hacia `awaiting_human` crea un evento con unicidad
`(business_id, phone_number, transition)`. Pollings repetidos no generan otro.
El outbox PostgreSQL registra una entrega por evento/suscripción y usa bloqueo
`SKIP LOCKED`; no necesita Redis. Respeta preferencias y acceso, desactiva 404/410,
y reintenta otros errores hasta cinco veces con backoff. Suscripciones nuevas no
reproducen eventos anteriores a su registro.

El frontend deberá instalar su service worker y presentar `url` del evento, un
deep link `/chats?business_id=...&phone_number=...&bucket=awaiting_human`.
Al mostrar Web Push debe usar `tag=payload.id` y `renotify=false` para deduplicar
también en el navegador. Una caída exactamente después de aceptar el proveedor
y antes del commit puede producir reenvío: Web Push no ofrece exactamente una
entrega extremo a extremo. La aplicación conserva un ID estable para este caso.

## Railway: servicios NUEVOS

1. Crear un PostgreSQL **exclusivo CRM** y dos servicios nuevos `api` y `sync-worker`.
   No cambiar el servicio del bot, su rama 42 ni sus variables.
2. Para ambos: repositorio actual, rama `43-CRM`, **Root Directory `/crm`**.
   Seleccionar `crm/railway.toml` para API y `crm/railway.worker.toml` para worker
   como config paths relativos al repositorio. Confirmar en el build que el
   Dockerfile proviene de `crm/`, nunca el launcher raíz del bot.
3. Configurar `CRM_DATABASE_URL` con el formato SQLAlchemy indicado y las variables
   de `.env.example` en los servicios nuevos. API y worker comparten solo la base
   CRM y configuración del CRM. El servidor frontend conserva el secreto JWT.
4. API ejecuta `alembic upgrade head` como pre-deploy; worker arranca con
   `python -m crm_service.worker`. API arranca Uvicorn y usa `/ready` como healthcheck.
5. Iniciar con notificaciones desactivadas. Verificar baseline y la operación
   según [el runbook](docs/RUNBOOK.md) antes de habilitarlas.

El Dockerfile ejecuta como usuario sin privilegios y no contiene secretos.
Build local: `docker build -t whatsapp-crm .`. En el cloud con CA de proxy,
usar `--secret id=proxy_ca,src="$CODEX_PROXY_CERT"`; no desactivar TLS.

## Validación

```bash
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/mypy crm_service
export CRM_TEST_DATABASE_URL=postgresql+psycopg://crm:replace_me@localhost:5432/crm_test
.venv/bin/pytest -q
```

La suite exige PostgreSQL real y una base desechable cuyo nombre empiece por
`crm_test`; no sustituye PostgreSQL por SQLite ni omite pruebas por falta de base.
Verifica upgrade/downgrade/upgrade y paridad Alembic/modelos, clasificación,
seguridad JWT y cross-tenant, cursores, fallos aislados, baseline silencioso,
idempotencia y Web Push simulado. Nunca contacta los Supabase de producción.
Los proveedores reales, Railway y el service worker del frontend requieren
validación de integración separada con credenciales y despliegue autorizados.
