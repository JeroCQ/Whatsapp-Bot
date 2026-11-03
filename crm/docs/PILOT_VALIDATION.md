# Validación local de atención operativa

Fecha: 2026-11-03. Rama `crm-staging-safety`, continuación de `ef74ef6`, sin
reconstruir el CRM. Bot: `c5531d0b53e8e77de5b41bb2f5c86a69a59c5390`. El commit
que contiene este informe incluye los cambios CRM y su guía de integración.

## Código entregado

- Bot: proveedor opt-in en human_support/config; autoridad durable en
  human_control_store y ambos SQL canónicos; rutas autenticadas en human_control_api;
  cercado de envíos/colas/seguimientos en human_send_gate/main; handoff bot/database;
  exportación GET por negocio en crm_source_api. Defaults existentes conservados.
- CRM: bot_gateway, ledger bot_commands y Alembic 0003_bot_commands; rutas de
  atención en API, modelos/cuerpos; lector bot_source en sync/settings;
  clasificación del proveedor CRM sin dependencia de conversation_id Chatwoot.
- Contrato: OpenAPI actualizado, tests de contrato/transporte, guía
  ../../docs/CRM_PILOT_HANDOFF.md, README, INTEGRATION, SOURCE_ACCESS, RUNBOOK y
  .env.example. El Dockerfile de Railway no cambia (blob
  f3f773179d0a20912ed1adfd6faff7d2d114211b).
- Aceptación opt-in: ../../pilot_tests/acceptance_pilot.py. No se incorpora a la
  suite ordinaria del bot para no imponer dependencias SQLAlchemy/PG del CRM al bot.

## Ejecución y resultados

PostgreSQL 17 local desechable en 127.0.0.1:55432. Las suites rechazan otro destino.
Comandos desde crm/, salvo cuando se indica la raíz:

```bash
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/mypy crm_service
CRM_TEST_DATABASE_URL=postgresql+psycopg://agent@127.0.0.1:55432/crm_test .venv/bin/python -m pytest -q
```

Resultado: lint y formato correctos; mypy correcto (20 archivos de servicio);
**210 tests CRM pasan**, incluyendo migraciones upgrade/downgrade, paridad de
metadata y upgrade con datos existentes; JWT/aislamiento/contrato, presupuestos,
fuentes sintéticas y permisos efectivos de lectores PostgreSQL dedicados.

Desde raíz:

```bash
PYTHONPATH=.:crm:tests CRM_TEST_DATABASE_URL=postgresql+psycopg://agent@127.0.0.1:55432/crm_test crm/.venv/bin/python -m pytest -q pilot_tests/acceptance_pilot.py
PYTHONPATH=. .venv/bin/python -m pytest -q tests
crm/.venv/bin/ruff check --select E4,E7,E9,F,I human_support.py human_control_store.py human_control_api.py human_send_gate.py crm_source_api.py tests/test_human_support_provider.py pilot_tests/acceptance_pilot.py
crm/.venv/bin/ruff format --check human_support.py human_control_store.py human_control_api.py human_send_gate.py crm_source_api.py tests/test_human_support_provider.py pilot_tests/acceptance_pilot.py
crm/.venv/bin/ruff check --select E9,F --ignore F401,F541,F841 main.py bot.py config.py database.py
python -m py_compile main.py bot.py config.py database.py
git diff --check
```

Aceptación: **14 tests pasan**, con PostgreSQL real para bot y CRM; solo transportes
Meta/Gemini simulados. Se ejecutan ambos scripts SQL completos con stubs locales
auth/storage de Supabase, además del bloque CRM en la suite. Upgrade repetido
preserva datos y caso tomado. Las funciones no conservan permisos para anon,
authenticated ni PUBLIC, incluso ante grants heredados de default privileges.
La concesión de ejecución a service_role queda verificada solo en esa base local.
Los roles de prueba se crean dentro de una transacción que se revierte.

Regresión bot: **1004 pasan, 3 omitidos, 1 falla previa**:
test_business_instruction_files::test_memos_instruction_is_available_to_the_selected_deployment.
El test espera una cuenta bancaria que no está en el archivo actual de Memo's;
ambos archivos están intactos. No se alteran instrucciones comerciales para
ocultar el fallo. Los cuatro módulos heredados conservan los mismos 8 hallazgos
ruff F401/F541/F841 que ef74ef6; se compararon diagnósticos antes/después. Los
módulos nuevos y CRM pasan lint sin esas exclusiones. No se afirma suite global verde.

Arranque verificado con proceso Uvicorn real en 127.0.0.1:58080, origen disabled,
PostgreSQL CRM migrado: GET /ready 200 y /health 200; apagado limpio después.
El modo disabled usado en ese smoke test no contacta fuentes.

## Qué queda demostrado

Cliente sintético → handoff automático → pausa persistida sin Chatwoot → toma con
propietario/versión → aceptación de respuesta humana por el transporte del bot →
recibo firmado de entrega → cierre → siguiente mensaje atendido por IA. Historial
asesor único y metadata que cubre todos los entrantes visibles al reservar.

Además: envío automático en ejecución mantiene pausing; envío humano bloqueado
hasta confirmación; doble clic/repetición una sola llamada Meta; timeout desconocido
sin reenvío; fallo explícito; receipt no firmado rechazado; callback opaco resuelve
el resultado incierto; ventana vencida; campos de destino inyectados rechazados;
JWT/empresa ajenos rechazados; exportación real del bot hacia inbox y todos los
ciclos; trabajo viejo cercado tras tomar/cerrar; seguimiento viejo y reset no
liberan control; SQL nuevo/upgrade con la misma definición. Un failed posterior a accepted vuelve
al inbox Por responder con el siguiente polling incremental, aunque la metadata
antecedente siga cacheada; callbacks repetidos o sent tardío no borran el fallo.
Los hechos de entrega no reabren casos cerrados ni cambian leído por actor.

## Límites y siguiente paso

No se ha probado recepción en un dispositivo real ni se han usado credenciales
productivas, Railway, Supabase remoto o Meta real. La CLI Railway 5.71.0 está instalada; railway whoami respondió Unauthorized y
no hay secretos piloto inyectados. Se guardó el requisito RAILWAY_TOKEN para entrada
segura por el usuario, pendiente de guardar/publicar la configuración. No se ha modificado Lovable, desplegado servicios
ni aplicado SQL en Briosos o los tres negocios activos. Briosos es el único piloto
autorizado por el usuario; ejecutar allí los pasos de la guía con acceso seguro.

Un resultado unknown puede mantener el caso bloqueado si nunca llega un recibo.
Se elige seguridad frente a duplicados; no existe una operación de resolución
forzada del intento. Las respuestas humanas son texto dentro de ventana, sin
adjuntos ni plantillas. La frescura del inbox depende del worker, mientras las
acciones consultan siempre al bot. Falta integrar el proxy/compositor Lovable y
verificar carga/latencia y recepción física antes del primer piloto real.

NOTIFICATIONS_ENABLED=false se mantiene exigido por configuración. Siguen
pendientes VAPID público/service worker, identidad individual y revocación;
no presentar un perfil por marca como empleado. No hay tareas globales ni métricas
SLA. Para otros negocios repetir revisión de esquema, aislamiento, secretos y
aceptación; esta entrega no autoriza activarlos ni promover ramas activas.
