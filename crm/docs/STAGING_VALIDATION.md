# Informe de continuación para staging

Fecha: 2 de noviembre de 2026. Rama de pruebas: `crm-staging-safety`.
Base conservada: `crm-railway-dockerfile@e451f5ca407910fd0fa5d9854173da5d5a0fc8bf`,
descendiente del backend `82c312b2418390220c8c90ac6a60f6bde794ed32`.

## Alcance

Se continuó el CRM existente. Todos los cambios son exclusivos de `crm/`.
No se modificó el código, SQL, roles, políticas, claves ni migraciones de los bots.
El Dockerfile conserva exactamente el blob `f3f773179d0a20912ed1adfd6faff7d2d114211b`
del arreglo e451f5c: no se reintrodujo el secret mount incompatible con Railway.
No se desplegó, promocionó ni hizo merge. No se contactaron fuentes productivas.
El commit de esta entrega pertenece únicamente a la rama nueva de pruebas.

## Entregables

- `docs/INTEGRATION.md` y `docs/openapi.json`: contrato obtenido de rutas y esquemas
  reales, inventario completo, JWT HS256, parámetros, cuerpos, respuestas, errores,
  cabeceras que elimina el proxy y capacidades expresamente ausentes.
- `source_postgres.py`, `security/isolated_source_proposal.sql` y
  `docs/SOURCE_ACCESS.md`: lector dedicado con ACL/RLS efectivos, auditoría y SQL
  separada para revisión. No es una migración de Supabase ni autorización productiva.
- `budget.py`, sincronizador existente ampliado y CLI worker: presupuestos validados,
  checkpoints por empresa/recurso/fila, backoff y métricas de lecturas/duración.
  Solo se reproyectan conversaciones modificadas; reconciliación histórica espaciada.
- `sync_jobs.py`, modelo y migración `0002_sync_controls`: API encola en la base CRM;
  el worker ejecuta sin que API necesite credenciales fuente. Idempotencia y locks
  coordinan solicitudes, y una empresa retirada no bloquea las demás.
- `synthetic.py`, `docs/STAGING.md`, `.env.example`, README y runbook actualizado:
  comandos reproducibles con dos bases fuente, una CRM, configuración por servicio,
  guardas de destinos y datos ficticios de ocho conversaciones.
- Pruebas de contrato, permisos, límites, reanudación, upgrade e integración; se
  conservaron las pruebas del CRM existente. El informe `VALIDATION.md` anterior
  permanece identificado como histórico.

## Verificación ejecutada

Entorno: Python 3.12 y PostgreSQL 17 desechable en loopback. Las bases y lectores
de prueba usan nombres reservados `crm_test*` / `crm_staging_*`; los archivos de
configuración sintética se generan fuera del checkout con permisos 0600.

| Verificación | Resultado |
| --- | --- |
| `ruff check .` | Aprobado |
| `ruff format --check .` | Aprobado |
| `mypy crm_service` | Sin errores en 17 módulos |
| `python -m pip check` | Sin dependencias incompatibles |
| `pytest -q` | **191 aprobadas, 0 fallidas, 0 omitidas**; una deprecación de Starlette/AnyIO |
| Migraciones | Base nueva, downgrade/upgrade y `alembic check` sin diferencias |
| Upgrade con datos existentes | Desde 0001 a 0002 conservando nota, etapa, asignación, etiquetas, versión y baseline; dirty/backoff/metrics inicializados |
| Permisos efectivos | Denegación real de DML/DDL/TEMP/funciones/pg_notify con transaction_read_only off; RLS, PUBLIC, ownership, membresías y tipos custom probados |
| CLI sintética | seed → worker --once por empresa → sync → verify satisfactorios con dos fuentes PostgreSQL reales de pruebas |
| API sin DSNs de fuente | Verificación con api.env: ocho conversaciones, aislamiento, JWT, paginación, leído, notas, tareas y conflicto 409 |
| Procesos por HTTP real | API Uvicorn y worker separados: solicitud 202 idempotente → lectura con cuenta dedicada → estado done |
| Efectos externos | Cero mensajes externos y cero eventos de notificación en el procedimiento sintético |
| Dockerfile y alcance Git | Dockerfile idéntico a e451f5c; sin cambios fuera del CRM |

Las regresiones cubren baseline parcial, presupuesto global entre tablas,
duración/requests, reanudación histórica por fila, backoff sin lecturas repetidas,
empresa lenta/fallida, solicitud de empresa retirada, rechazo de DSNs/logins
compartidos, ausencia de reproyección histórica en pollings sin cambios y fallo
de una ejecución nueva que no queda oculto por un baseline anterior completo.

## Límites y requisitos pendientes

1. **Fuentes productivas bloqueadas.** No se demuestra ni afirma que un rol nuevo
   pueda evitar permisos PUBLIC del Supabase actual sin afectar a los bots. Se
   requiere revisión independiente de acceso/infraestructura, fuente aislada o
   garantías equivalentes y un cambio revisado antes de permitir conectividad remota.
2. La propuesta SQL se ejecutó solo en bases sintéticas nuevas con marker. No debe
   copiarse al Supabase de una marca: revoca ACL compartidas únicamente en esa
   fachada aislada. No se usa default_transaction_read_only como autorización.
3. Presupuesto de tiempo cooperativo con consultas acotadas; no es un deadline duro
   a nivel de proceso. Hay granularidad del driver y CPU local entre comprobaciones.
   Historiales mayores al techo fallan explícitamente; no se ensayó carga productiva.
4. `NOTIFICATIONS_ENABLED=false` se exige en configuración. Faltan contrato de clave
   pública VAPID, service worker, consentimiento, identidad estable individual y
   revocación. Un profile por marca es compartido, no una identidad por empleado.
5. No hay listado global de tareas, métricas de tiempos/SLA, administración de
   empleados/revocación ni acciones del bot. Las respuestas OpenAPI amplias se
   complementan con pruebas HTTP; no se prometen capacidades futuras.
6. Falta integrar y revisar el proxy frontend, aprobar secretos por servicio,
   restauración, observabilidad y carga. No se autoriza un despliegue o piloto
   real por el resultado de estas pruebas. Los procesos HTTP de validación se
   detuvieron al terminar; ninguna fuente real participó.

Los comandos exactos y la configuración por servicio están en README y STAGING.md;
los requisitos detallados del primer piloto figuran en RUNBOOK.md.
