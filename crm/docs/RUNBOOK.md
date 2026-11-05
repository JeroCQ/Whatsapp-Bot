# Operación de staging y requisitos del piloto

## Fronteras

Usar exclusivamente crm-staging-safety, derivada de e451f5c, preservando Dockerfile.
No hacer push/merge a ramas activas. El despliegue posterior autorizado se limita
al piloto Briosos; aquí no se ha desplegado. Estado de gestión reside en CRM;
estado de atención y envíos reside en el bot. Migración CRM: 0003_bot_commands.
El esquema nuevo del bot y las instrucciones Railway/Lovable están en
[CRM_PILOT_HANDOFF.md](../../docs/CRM_PILOT_HANDOFF.md).

Las fuentes sintéticas mantienen NOTIFICATIONS_ENABLED=false. Para el CRM
autorizado, Web Push tiene activación explícita descrita más abajo. El modo bot_api soporta exportación real
por un secreto GET dedicado del bot, distinto del de control. Fuentes PostgreSQL
directas siguen limitadas a loopback marcado, incluso en postgres_readonly.
No reutilizar SERVICE_ROLE_KEY ni otras claves administrativas del bot.

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
  que se deban conservar. La atención operativa sí necesita el esquema del bot,
  revisado y aplicado solo al piloto según su guía; el modo sintético no.
- Corregir acceso/configuración y esperar next_retry_at; repetir es idempotente.
  No borrar mensajes/notas/cursores para corregir un error de red.
- Para rehacer fixtures, usar solo loader contra bases vacías/marcadas; nunca como
  recuperación productiva. El lector dedicado vuelve a auditar permisos.
- Rollback: detener solo API/worker del piloto; no volver a código bot anterior
  con casos humanos abiertos o envíos inciertos. Cerrar/drenar con autoridad
  actual primero. Los bots reciben sin CRM; un caso humano permanece pausado.
- Rotar JWT y secreto interno de forma coordinada. Son distintos; JWT también
  invalida cursores y no tiene keyring. No revocar credenciales utilizadas por bots.

## Requisitos antes del primer piloto real

1. Para bot_api, revisar y activar solo la fachada Briosos y secretos dedicados
   según la guía piloto. Para PostgreSQL directo, revisar por separado
   [la propuesta de acceso](SOURCE_ACCESS.md); PUBLIC/funciones compartidas impiden
   afirmar lectura efectiva con solo un rol nuevo. Ese acceso remoto sigue bloqueado.
2. Aprobar infraestructura, credenciales dedicadas y conectividad. No SERVICE_ROLE
   en CRM ni pruebas de escrituras denegadas contra fuentes productivas.
3. Integrar proxy conforme al [contrato](INTEGRATION.md), resolver permisos servidor,
   eliminar Origin/Sec-Fetch-Site, aislar caché por perfil y proteger secretos.
4. Definir identidad estable por empleado y revocación. Un perfil compartido por
   marca no es identidad individual. No hay endpoints de alta/revocación ni canal
   automático de revocación de notificaciones.
5. Validar `GET /v1/push/config`, el service worker y la revocación del dispositivo
   antes de dar por activa la entrega. Un crash entre
   aceptación push y commit puede repetir entrega: no hay garantía exactly-once.
6. Acordar capacidades ausentes: tareas globales, métricas de tiempos/SLA, adjuntos
   humanos y plantillas fuera de ventana. Las acciones del bot implementadas
   requieren confirmar versión/pausa y tratar unknown sin reenvío automático.
7. Ensayar restauración, carga y alertas de presupuesto/frescura con datos aprobados.
   La validación local no autoriza promoción ni un despliegue.

## Activación Web Push

Aplica al API y sync existentes, tanto en instalaciones nuevas como actualizadas;
no requiere SQL ni nuevas migraciones. Las fixtures siempre permanecen apagadas.

1. Reutilizar un par VAPID P-256 ya configurado, si existe. Si no existe, generar
   el par una sola vez fuera de Git y conservar la clave privada en el canal de
   secretos del hosting; no imprimirla en chat/logs. Se acepta el valor privado
   base64url de 32 bytes o su PEM completo, nunca una ruta a archivo del servidor.
2. En **API y sync**, configurar privadamente el mismo `VAPID_PRIVATE_KEY` y un
   `VAPID_SUBJECT=mailto:<correo-operativo-real>`, y luego
   `NOTIFICATIONS_ENABLED=true`. El frontend no necesita una variable VAPID propia:
   recibe únicamente la clave pública de `/api/crm/push/config`.
3. Después de actualizar ambos servicios y publicar el frontend revisado, iniciar
   sesión en el dominio HTTPS final. Consultar el estado real del endpoint y
   aceptar la invitación de notificaciones. En iPhone/iPad, Web Push exige la app
   instalada en pantalla de inicio en una versión compatible; el permiso nativo
   requiere una acción del usuario y no se puede forzar tras un efecto de React.
4. Confirmar un handoff **nuevo** posterior al baseline, una notificación con
   enlace a su empresa/conversación y ausencia de repetición al seguir polling.
   No se notifica por backfill ni por suscripciones creadas después del evento.
   Cerrar/cambiar cuenta debe revocar el dispositivo y su vinculación del SW.
5. Rollback de notificaciones: `NOTIFICATIONS_ENABLED=false` en API y sync. No
   cambia la recepción del bot, no elimina la base CRM y no reenvía mensajes.

La cuenta Admin y los perfiles compartidos siguen siendo identidades de perfil,
no empleados individuales. El servidor valida alcance/preferencias y permite
eliminar la suscripción propia. Rotar una contraseña no revoca automáticamente
suscripciones previamente registradas; no hay revocación global de identidades.
Un permiso concedido o una suscripción creada no prueban recepción física. Quedan
por probar el hosting final, proveedores Web Push e iPhone/Android reales.
