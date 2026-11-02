# Operación del CRM

## Límites y referencias

El CRM tiene su propio esquema y migraciones en `crm/alembic`. Para instalaciones
nuevas y para futuras actualizaciones se usa únicamente `alembic upgrade head`
contra `CRM_DATABASE_URL`. No se ejecuta ninguna migración del bot, tanto en marcas
existentes como futuras. No hacer commits ni PRs como parte de esta implementación.

Revisados: `AGENTS.md` del bot en `43-CRM`, esquema y código de handoffs,
metadata de mensajes, y `AGENTS.md`/perfiles del frontend en la baseline indicada.
El frontend resuelve acceso del lado servidor; su parámetro de empresa elegido
por el navegador no otorga permisos al CRM.

## Backfill inicial

1. Crear PostgreSQL CRM vacío, registrar empresas y comprobar que los orígenes
   sean distintos. Añadir sus destinos HTTPS al egress del entorno si aplica.
2. Ejecutar migraciones CRM. Mantener `NOTIFICATIONS_ENABLED=false` en ambos
   servicios. Obtener `/ready` satisfactorio.
3. Iniciar worker o invocar `POST /internal/sync` con su secreto interno desde
   un entorno seguro. No publicar ese secreto ni tokens en historial/logs.
4. Consultar `/v1/sync-status` con un JWT servidor de perfil autorizado. Verificar
   `baseline_complete=true` y `last_error=null` para cada empresa. Comparar muestras
   de mensajes y buckets contra lecturas del origen y Chatwoot, sin enviar mensajes.
5. Registrar agentes, preferencias y suscripciones, configurar VAPID, y después
   activar `NOTIFICATIONS_ENABLED=true`. No se notifican pendientes históricos.
6. Verificar una transición sintética en un origen **de pruebas**. No provocar
   handoffs o mensajes reales para probar el CRM en producción.

Los mensajes usan watermark numérico por empresa. Una reconciliación rodante
de una página por polling recoge ediciones antiguas y commits tardíos con ID
menor al watermark; su latencia depende del volumen histórico. Las otras tablas
se recorren completas por keyset `phone_number` en cada poll porque no hay un
timestamp de actualización fiable en todos los despliegues. Los cursores y la
proyección se confirman juntos por empresa. Un fallo revierte esa empresa y el
worker continúa las demás. Los locks transaccionales evitan sync simultáneo del
mismo negocio entre el worker y el endpoint interno.

El límite de seguridad es 10.000 páginas por recurso/poll con páginas de 500.
Un backfill mayor se detiene con un código explícito sin marcar baseline completo;
ajustar el límite en una revisión operativa antes de repetir. No ignorar fallos
ni asumir que un baseline parcial es completo.

## Operación y calidad de datos

- Monitorizar `/ready`, `last_success`, `last_error`, `baseline_complete`,
  `data_quality_warnings` y `stale` en `/v1/sync-status` (umbral fijo: cinco minutos).
- Una tabla opcional ausente o columna opcional inexistente produce una advertencia
  y continúa. Una identidad/cursor de mensaje indispensable ausente impide marcar
  baseline de esa empresa: no es seguro fabricar IDs o clasificar sin el historial.
- La falta de estado de pausa se trata como atención con advertencia; no inferir
  que el bot está activo ni cerrar silenciosamente.
- La sincronización no es un snapshot transaccional entre tablas remotas. Un
  handoff o mensaje que cambia durante el barrido converge en el siguiente poll.
  Los borrados remotos no se propagan automáticamente: el CRM conserva su copia.
- Un fallo en una empresa no marca sanas sus credenciales ni detiene el resto.
  Los intentos se procesan secuencialmente con timeouts de red acotados.
- Cambios CRM se auditan con perfil, empresa, acción e identificador/campos; sin
  cuerpos de notas, mensajes, tokens o claves de suscripción en logs.
- Ejecutar API con `--no-access-log`: el path de una conversación contiene teléfono.
  No habilitar DEBUG de HTTPX/HTTPcore/SQLAlchemy en producción.
- El registro de acceso para Web Push conserva el último alcance JWT observado
  del perfil. Al revocar acceso, actualizar ese perfil mediante un JWT vigente
  con su alcance reducido o desactivar sus suscripciones desde la administración
  de la base CRM; no existe propagación automática de revocaciones desde Lovable.

## Reconciliación manual y recuperación

Primero corregir acceso, schema remoto o configuración; invocar el sync interno.
La repetición conserva unicidad de mensajes, campos CRM y eventos. Un fallo antes
del commit no avanza cursores. No limpiar mensajes para corregir un error de red.

Para rehacer intencionalmente baseline de una empresa (sin notificaciones), detener
worker, desactivar notificaciones y realizar **solo en PostgreSQL CRM**, con backup:

```sql
BEGIN;
UPDATE sync_status SET baseline_complete = false WHERE business_id = 'empresa_elegida';
DELETE FROM sync_cursors WHERE business_id = 'empresa_elegida';
COMMIT;
```

Reiniciar worker y comprobar baseline. Se conservan notas, tareas, asignaciones,
mensajes y auditoría. No ejecutar estas instrucciones contra Supabase origen.

## Web Push

Verificar VAPID antes de habilitar. Los eventos se generan aunque todavía no haya
suscripciones; el historial in-app no depende del proveedor. Cada entrega tiene
estado `pending`, `sent`, `failed`, `disabled` o `expired`. Cinco fallos agotan el
intento; la siguiente transición puede crear otro evento. 404/410 desactiva la
suscripción. El usuario puede registrarla de nuevo con su sesión válida.

El outbox impide duplicados ordinarios y los locks coordinan varios dispatchers.
Una caída entre aceptación del proveedor y commit sigue permitiendo entrega
repetida. El service worker debe deduplicar por `payload.id` y usar notification
`tag`. No prometer entrega única, entrega garantizada ni soporte push en todos
los navegadores. Sin service worker del frontend solo funciona el historial in-app.

## Rollback

1. Desactivar notificaciones y detener **solo** `api`/`sync-worker` del CRM.
   El bot y Chatwoot continúan operando sin dependencia del CRM.
2. Restaurar la imagen/configuración CRM anterior compatible con el esquema.
   No borrar la base ni aplicar `alembic downgrade base` en producción.
3. Para recuperación de datos usar un backup de PostgreSQL CRM y validar `/ready`,
   baseline y muestras antes de reabrir. Los operadores deben programar backups
   con `pg_dump` del CRM y ensayar restauración en otra base aislada.
4. Repetir sincronización idempotente. Mantener notificaciones apagadas durante
   una restauración que pueda reponer entregas ya enviadas.

## Rotación de credenciales

- Fuente Supabase: rotar una empresa por vez en su configuración segura,
  actualizar API/worker del CRM, reiniciar y verificar lecturas y `last_success`.
  La creación/rotación de la clave del origen debe coordinarse con su propietario;
  no cambiar ni revocar una credencial que siga usando el bot.
- JWT: cambiar el secreto compartido en servidor frontend y API dentro de una
  ventana coordinada. Los JWT duran como máximo cinco minutos; no hay keyring ni
  solapamiento de claves en esta versión. Invalidará también cursores firmados.
- Sync: rotar por separado y actualizar únicamente el llamador interno y CRM.
- VAPID: cambiar la clave implica volver a suscribir los navegadores con la nueva
  clave pública; desactivar suscripciones antiguas. No imprimir la clave privada.
- Base CRM: rotar el usuario exclusivo, reiniciar pools y verificar migraciones y
  `/ready`. No reutilizar roles o credenciales de un Supabase de origen.

## Alcance de la validación

Se prueban PostgreSQL real, migraciones, arranque local, API autenticada y sync
con transportes sintéticos. Las lecturas contra los tres Supabase reales, Web Push
real, configuración de red/secretos Railway y frontend servidor/service worker
son verificaciones posteriores al suministro seguro de sus credenciales. Las
estadísticas de analytics son conteos actuales, no reporting histórico. No hay
ensayo de carga ni objetivo de latencia garantizado para grandes historiales.
