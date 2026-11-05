# Procedimiento integral exclusivamente sintético

Este procedimiento continúa el CRM existente. No usa Meta, Chatwoot, Web Push,
Supabase productivos ni credenciales administrativas de bots. Se ejecuta en una
máquina local aislada con PostgreSQL **desechable**, desde `crm/`.

El loader exige conexión TCP loopback (`127.0.0.1`, `::1` o `localhost`) sin
opciones de conexión en query string. Verifica también que el servidor PostgreSQL
se identifique como loopback, para rechazar un túnel que termine en un servidor
remoto. La base CRM debe comenzar por `crm_test` o `crm_staging_`; cada origen debe
comenzar por `crm_test_source_` o `crm_staging_source_`. Las tres bases son distintas.
Solo admite bases vacías o previamente marcadas por este procedimiento; no marca
ni sobrescribe una base con tablas, vistas, esquemas o rutinas ajenas. Esto no autoriza probar
escrituras prohibidas contra destinos reales, aunque se acceda a ellos por un túnel.

## Preparar tres bases nuevas

Instalar Python 3.12, PostgreSQL 17 y las dependencias de desarrollo del CRM. En el
ejemplo, el PostgreSQL de pruebas escucha solo en `127.0.0.1:55432`; el usuario
`agent` es el propietario **del clúster local desechable**. Si el usuario local es
otro, ajustar únicamente ese nombre. No usar URLs de Supabase ni del Railway actual.

```bash
cd crm
python3.12 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
createdb -h 127.0.0.1 -p 55432 -U agent crm_staging_demo
createdb -h 127.0.0.1 -p 55432 -U agent crm_staging_source_one
createdb -h 127.0.0.1 -p 55432 -U agent crm_staging_source_two
export CRM_STAGING_CRM_ADMIN_URL=postgresql+psycopg://agent@127.0.0.1:55432/crm_staging_demo
export CRM_STAGING_SOURCE_ONE_ADMIN_URL=postgresql+psycopg://agent@127.0.0.1:55432/crm_staging_source_one
export CRM_STAGING_SOURCE_TWO_ADMIN_URL=postgresql+psycopg://agent@127.0.0.1:55432/crm_staging_source_two
export NOTIFICATIONS_ENABLED=false
staging_config_dir=$(mktemp -d /tmp/crm-staging-config.XXXXXX)
.venv/bin/python -m crm_service.synthetic seed --output-dir "$staging_config_dir"
```

El loader aplica **solo** migraciones CRM a la base CRM. En las dos bases fuente
nuevas crea las cuatro tablas mínimas y datos ficticios, y ejecuta la propuesta
`security/isolated_source_proposal.sql` descrita en [SOURCE_ACCESS.md](SOURCE_ACCESS.md).
Esa propuesta cambia PUBLIC/RLS/roles únicamente en estas bases desechables;
**nunca** debe trasladarse al Supabase de un bot. Genera un lector LOGIN dedicado
por origen, con contraseña sintética aleatoria, sin reutilizar SERVICE_ROLE_KEY.

Genera `api.env` y `worker.env` con permisos `0600`, fuera del checkout. `api.env`
no contiene credenciales de fuentes; `worker.env` contiene las dos conexiones
dedicadas de lectura. No sobrescribe archivos de configuración existentes. La
carpeta generada y sus claves son efímeras, exclusivamente de pruebas.

## Sincronizar y probar la API real

El comando `sync` ejecuta el sincronizador existente con las conexiones de lectura
reales a las dos bases PostgreSQL sintéticas. Repite ejecuciones acotadas hasta
completar ambos baseline, sin notificaciones. Falla si no converge o alguna empresa
falla; no declara completo un baseline parcial.

```bash
set -a
. "$staging_config_dir/worker.env"
set +a
.venv/bin/python -m crm_service.synthetic sync
# La verificación de API no necesita DSNs de fuente; usar api.env en otra terminal limpia:
# set -a; . "$staging_config_dir/api.env"; set +a
.venv/bin/python -m crm_service.synthetic verify
```

`verify` carga solo configuración API y exige el marker CRM sintético antes de
cualquier escritura de prueba. Ejecuta las rutas FastAPI reales mediante TestClient en el mismo proceso,
contra la base PostgreSQL CRM migrada. No apunta a una URL API externa ni envía
mensajes. Rechaza Python con optimización (`-O`) para que sus comprobaciones no se
puedan omitir. Comprueba:

- JWT HS256 válido y ausencia de token (401), y rechazo de empresa fuera de scope (403).
- Inbox con paginación por cursor de una conversación por página, sin duplicados.
- Ocho conversaciones: pendiente, respondida por asesor, cerrada y reabierta en cada empresa.
- El mismo teléfono `573000000001` existe en ambas empresas, con nombre/metadata distintos.
- Leído por actor en una empresa no marca la otra; no cambia `awaiting_human`.
- Cambios CRM, conflicto de versión (409), creación/lectura/edición/borrado de notas y tareas.
- Cero eventos de notificación; no se invoca el dispatcher ni ningún transporte de mensajes.

Los únicos identificadores de empresas aceptados por los fixtures son
`synthetic_one` y `synthetic_two`; los teléfonos y mensajes están marcados como
SYNTHETIC. Repetir `sync` conserva idempotencia; repetir `verify` usa la versión
CRM vigente. La recarga de datos es una operación de fixtures, no de sincronización:
requiere de nuevo las URLs ADMIN **locales** y un directorio de configuración nuevo.

## Arranque por servicios

Para API, abrir una terminal limpia y cargar únicamente `api.env`:

```bash
set -a
. "$staging_config_dir/api.env"
set +a
.venv/bin/uvicorn crm_service.api:create_app --factory --host 127.0.0.1 --port 8080 --no-access-log
```

Para worker, abrir otra terminal limpia y cargar únicamente `worker.env`:

```bash
set -a
. "$staging_config_dir/worker.env"
set +a
.venv/bin/python -m crm_service.worker --once --business synthetic_one
# Para continuar de forma periódica (terminar con Ctrl+C):
.venv/bin/python -m crm_service.worker
```

`--once` ejecuta un paso con presupuesto, no un backfill sin límite. Si informa
trabajo parcial, volver a invocarlo o dejar el worker periódico hasta completar
el baseline. Consultar `/v1/sync-status`; una respuesta HTTP de solicitud de sync
no significa que el baseline terminó. Los presupuestos configurables están
documentados en el README del worker. No colocar DSNs de origen en `crm-api`
para usar `/internal/sync`: ese endpoint solo debe solicitar trabajo en la base
CRM, y el proceso worker es quien dispone de credenciales de lectura.

Mantener siempre `NOTIFICATIONS_ENABLED=false` en el flujo sintético, que no
necesita VAPID ni suscripciones. La activación Web Push del CRM autorizado usa
el contrato `/v1/push/config` y [el runbook](RUNBOOK.md#activación-web-push).
Un perfil compartido por marca o Admin no representa a cada empleado de forma
individual; alta/baja y revocación global de empleados siguen sin implementarse.

## Suite automatizada

```bash
export CRM_TEST_DATABASE_URL=postgresql+psycopg://agent@127.0.0.1:55432/crm_test
.venv/bin/pytest -q tests/test_staging_e2e.py
```

La prueba integral crea tres bases con nombres aleatorios de pruebas y lectores
dedicados; al terminar los elimina. Verifica tanto la lectura real como los
rechazos de destinos inseguros, el bloqueo de notificaciones, los permisos de
archivos generados y el rechazo de una base existente sin marker. Requiere un
propietario del clúster **desechable** capaz de crear/borrar esas bases y roles.
Nunca ejecutar esta suite con credenciales administrativas de un sistema real.
