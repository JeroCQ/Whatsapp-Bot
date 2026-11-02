# Operación de staging y requisitos del piloto

## Fronteras

Usar exclusivamente crm-staging-safety, derivada de e451f5c, preservando Dockerfile.
No hacer push/merge a ramas activas ni desplegar. Todo estado nuevo reside en CRM.
Migración vigente: 0002_sync_controls; no usar migraciones ni roles de los bots.

Mantener NOTIFICATIONS_ENABLED=false. Solo fuentes de pruebas loopback marcadas
con lectores dedicados pueden activarse. Producción permanece bloqueada incluso
en modo postgres_readonly. No reutilizar SERVICE_ROLE_KEY ni otras claves del bot.

## Backfill controlado

Seguir [STAGING.md](STAGING.md) para las tres bases y datos ficticios. API utiliza
api.env; worker utiliza worker.env. ADMIN del loader no debe llegar a los servicios.

Un paso puntual, desde crm/ y una terminal limpia con worker.env cargado:

```bash
.venv/bin/python -m crm_service.worker --once --business synthetic_one
```

Para solicitarlo desde API: POST /internal/sync, X-CRM-Sync-Secret, cuerpo:

```json
{"business_id":"synthetic_one","idempotency_key":"baseline-demo-1"}
```

Devuelve 202 y una solicitud persistida. GET /internal/sync/{id} con la misma
cabecera permite seguirla. pending puede contener resultado partial/backoff;
done indica que completó un ciclo, no un snapshot transaccional remoto. Una clave
repetida retorna la misma solicitud; tras un fallo diagnosticado usar una nueva
clave para solicitar otro intento. API nunca abre conexiones de fuentes.

El worker usa locks PostgreSQL por negocio y SKIP LOCKED para solicitudes. Si
muere antes del commit, la solicitud pendiente vuelve a estar disponible. Un
negocio parcial/fallido no impide pasos de los demás. El README detalla límites
validados de páginas, requests, duración, conexiones y proyecciones.

## Diagnóstico

/v1/sync-status expone run_state, baseline, last_success, last_error, next_retry_at,
métricas y advertencias por empresa. partial preserva cursores sin completar un
baseline parcial. failed aplica backoff exponencial; backoff no contacta fuentes.
No insistir con presupuestos insuficientes para auditoría y una página (mínimo
cuatro requests). Ajustar límites únicamente con fuentes sintéticas.

conversation_history_limit_requires_review significa que el historial excede el
máximo; nunca se ocultan mensajes para fabricar una clasificación. La reconciliación
vieja avanza una página por intervalo y tiene latencia proporcional al volumen.
No loguear URLs con claves, JWT, cuerpos de notas/mensajes o suscripciones. API
arranca con --no-access-log; no habilitar DEBUG HTTPX/SQLAlchemy. Auditoría CRM
registra perfil/empresa/acción/campos sin contenido completo.

## Recuperación y rotación de staging

- Backup de CRM antes de migrar; alembic upgrade head. No downgrade base sobre datos
  que se deban conservar. Ningún SQL del bot se necesita para este cambio.
- Corregir acceso/configuración y esperar next_retry_at; repetir es idempotente.
  No borrar mensajes/notas/cursores para corregir un error de red.
- Para rehacer fixtures, usar solo loader contra bases vacías/marcadas; nunca como
  recuperación productiva. El lector dedicado vuelve a auditar permisos.
- Rollback: detener solo API/worker de staging, volver a código compatible con el
  esquema o restaurar una copia CRM. Los bots no dependen del CRM.
- Rotar JWT y secreto interno de forma coordinada. Son distintos; JWT también
  invalida cursores y no tiene keyring. No revocar credenciales utilizadas por bots.

## Requisitos antes del primer piloto real

1. Revisión separada con propietario de datos de [la propuesta de acceso](SOURCE_ACCESS.md).
   PUBLIC/funciones de un Supabase compartido impiden afirmar lectura efectiva con
   solo un rol nuevo. Hace falta una fuente aislada endurecida o revisión externa
   demostrable sin alterar ACL de bots. Levantar bloqueo remoto requiere otro cambio
   revisado; no existe una variable para saltarlo en esta rama.
2. Aprobar infraestructura, credenciales dedicadas y conectividad. No SERVICE_ROLE
   en CRM ni pruebas de escrituras denegadas contra fuentes productivas.
3. Integrar proxy conforme al [contrato](INTEGRATION.md), resolver permisos servidor,
   eliminar Origin/Sec-Fetch-Site, aislar caché por perfil y proteger secretos.
4. Definir identidad estable por empleado y revocación. Un perfil compartido por
   marca no es identidad individual. No hay endpoints de alta/revocación ni canal
   automático de revocación de notificaciones.
5. Mantener notificaciones apagadas hasta definir clave pública VAPID (sin endpoint
   actual), service worker, suscripciones, revocación y deduplicación. Un crash entre
   aceptación push y commit puede repetir entrega: no hay garantía exactly-once.
6. Acordar capacidades ausentes: tareas globales, métricas de tiempos/SLA y acciones
   del bot no existen. No construir UI que dependa de endpoints ficticios.
7. Ensayar restauración, carga y alertas de presupuesto/frescura con datos aprobados.
   La validación local no autoriza promoción ni un despliegue.
