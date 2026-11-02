# Informe de implementación y validación

Fecha: 2 de noviembre de 2026. Rama de trabajo: `43-CRM`.

## Resultado

Backend CRM independiente implementado exclusivamente en `crm/`. El código del
bot, sus migraciones y el frontend no se modificaron. No se hicieron commits,
PRs, cambios en producción, envíos Meta ni operaciones contra Supabase reales.

Incluye API FastAPI autenticada mediante JWT HMAC, PostgreSQL propio, migración
Alembic, registro extensible de empresas, adaptador PostgREST de solo lectura,
sync incremental y reconciliación, clasificación pura, estados exclusivamente
CRM, auditoría y outbox in-app/Web Push sin Redis. API y worker tienen entradas
y configuraciones de Railway separadas. El handoff manual está deshabilitado.

## Archivos creados

```text
crm/
  .dockerignore
  .env.example
  Dockerfile
  README.md
  pyproject.toml
  railway.toml
  railway.worker.toml
  conftest.py
  alembic.ini
  alembic/
    env.py
    script.py.mako
    versions/0001_crm_independent_crm_schema.py
  crm_service/
    __init__.py
    api.py
    classification.py
    db.py
    models.py
    notifications.py
    schemas.py
    security.py
    settings.py
    source.py
    sync.py
    worker.py
  tests/
    __init__.py
    conftest.py
    test_api.py
    test_classification.py
    test_migrations.py
    test_notifications.py
    test_settings.py
    test_source.py
    test_sync.py
  docs/
    RUNBOOK.md
    VALIDATION.md
```

35 archivos de entrega. Entornos virtuales, caches, bases de pruebas y
configuración local generada permanecen ignorados o fuera del checkout.
El `conftest.py` de `crm/` separa la colección de esta suite de la del bot;
ambas suites se ejecutaron por separado, sin ocultar fallos de ninguna.

## Comprobaciones ejecutadas

| Comprobación | Evidencia / resultado |
| --- | --- |
| Instalación documentada | `python -m pip install -e '.[dev]'` satisfactorio en el entorno virtual CRM |
| Dependencias | `python -m pip check`: sin dependencias incompatibles |
| Lint | `ruff check .`: aprobado |
| Formato | `ruff format --check .`: aprobado |
| Typecheck | `mypy crm_service`: sin errores en 12 módulos |
| Suite CRM | **104 aprobadas, 0 fallidas, 0 omitidas** con PostgreSQL 17 real; una advertencia de deprecación de Starlette/AnyIO |
| Migraciones | Upgrade en base nueva; downgrade a base y nuevo upgrade en base desechable; `alembic check` sin diferencias |
| API local por HTTP | `/health`, `/ready`, capabilities, inbox, pipeline, analytics y sync-status con 200; cambios CRM y leído/no leído verificados con datos sintéticos de `localdemo` |
| Autenticación | JWT válido/incorrecto/vencido, audiencia/issuer/algoritmo, alcance por empresa y cursores firmados probados |
| Docker | Imagen construida con TLS verificado y CA del proxy, sin copiar secretos a capas |
| Imagen final | API y sync-worker en contenedores distintos, UID 10001, PostgreSQL desechable en red Docker aislada; `/health=200`, `/ready=200`, inbox sin JWT=401 y con JWT=200 |
| Esquema dentro de Docker | `alembic upgrade head` y `alembic check` satisfactorios |
| Regresión del bot | **975 aprobadas, 3 omitidas, 1 fallo preexistente**, igual que la baseline de onboarding |
| Aislamiento de archivos | `git diff --exit-code` sin cambios en archivos preexistentes; solo archivos nuevos bajo `crm/` |

El fallo preexistente del bot es
`tests/test_business_instruction_files.py::test_memos_instruction_is_available_to_the_selected_deployment`:
el número de cuenta esperado por el test difiere del documento de instrucciones
de Memo’s. No se alteró el test ni el documento para corregirlo dentro de este trabajo.

Las pruebas cubren los casos de clasificación solicitados, actividad posterior al
cierre, handoff reutilizado, matching por metadata y orden legacy; idempotencia,
cursores separados, fallos aislados, rollback de página incompleta, rechazo de
mutación de identidad de mensaje y mantenimiento de campos CRM entre syncs.
Cubren además métodos prohibidos antes del transporte, RPC/redirecciones/orígenes
ajenos, baseline sin eventos, transiciones posteriores, deduplicación y preferencias
push, reintentos y desactivación 404/410, más CRUD y auditoría de la API.

## Límites y verificaciones pendientes de integración

- No se proporcionaron credenciales de los Supabase de Tanaka, Memo’s y Velvet;
  sus lecturas reales no se ejecutaron. Los conectores se verificaron con HTTPX
  MockTransport y la sincronización con datos sintéticos y PostgreSQL real.
- El proceso worker real y el contenedor arrancaron correctamente. Sin secretos
  de origen informan `source_configuration_missing`, sin fingir una sincronización
  real ni impedir que la API o las demás empresas puedan operar.
- No se desplegó en Railway ni se cambió el frontend. Se entregan Dockerfile,
  configuración de ambos servicios, contrato JWT y runbook; faltan configurar
  servicios nuevos, secretos, conectividad y el proxy servidor del frontend.
- Web Push real requiere VAPID, suscripciones y service worker del frontend.
  La entrega se probó con un emisor simulado. Un crash entre aceptación por el
  proveedor y commit puede repetir entrega; se conserva ID/tag para deduplicación.
- No hay prueba de carga. Estados/handoffs requieren barridos paginados porque los
  orígenes legacy no ofrecen un cursor de actualización fiable. La reconciliación
  rodante de mensajes antiguos tiene latencia proporcional al historial.
- El CRM conserva copias y no propaga borrados remotos automáticamente. La
  sincronización entre tablas remotas es eventualmente consistente.
- La revocación de acceso para notificaciones depende del último alcance de
  perfil observado; consultar el runbook para revocación y rotación operativas.

La implementación y validación local están terminadas. Esto no constituye una
afirmación de integración o disponibilidad en producción.
