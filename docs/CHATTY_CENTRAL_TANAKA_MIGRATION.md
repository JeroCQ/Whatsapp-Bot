# Consolidación de Chatty en Tanaka

Esta rama prepara el destino y la transferencia. El punto de corte sigue bloqueado
hasta disponer de ejecución autenticada en los runtimes existentes y registrar
evidencia del drenado de todos los procesos. Los modos actuales permanecen en
origen y las claves continúan en sus servicios.

## Preparación

- Tanaka mantiene las tablas públicas sin prefijo. Briosos, Memo’s y Velvet
  conservan sus contratos de tablas/RPC con los prefijos `chatty_briosos_`,
  `chatty_memos_` y `chatty_velvet_`. Las tablas de Memo’s y Velvet y las del
  registro ya existen. Antes de preparar Briosos, obtener de su origen
  `scripts/central_schema_manifest.sql` y renderizarlo con
  `scripts/render_central_storage.py`. Confirmar columnas, claves, enum, secuencias
  y permisos del origen real antes de aplicar DDL. Conservar `auth.users` y verificar
  que los UUID referidos por `dashboard_admins` existan en el destino.
- Desde `/crm`, `python -m crm_service.central_migrate` comprueba el historial
  y usa Alembic únicamente en el esquema privado `chatty_crm` del proyecto
  `tbvcvqddpppqlwuehdaf`. La conexión privada `CHATTY_CENTRAL_DATABASE_URL`
  debe apuntar directamente a ese proyecto o a su pooler de sesión. Mantener
  fuera el `preDeployCommand` comercial de Alembic al cablear el destino central.
- Para cada origen, ejecutar `python -m crm_service.central_copy prepare
  --epoch <id> --dataset <origen>` con el mismo identificador. Los orígenes son
  `briosos`, `memos`, `velvet`, `commercial-crm` y `pilot-crm`. Inyectar
  privadamente su conexión PostgreSQL existente como
  `CHATTY_MIGRATION_SOURCE_DATABASE_URL`. En los bots, la URL y el rol existentes
  de Supabase se referencian como `CHATTY_MIGRATION_SOURCE_STORAGE_URL` y
  `CHATTY_MIGRATION_SOURCE_STORAGE_KEY`. Para Storage destino,
  `CHATTY_CENTRAL_STORAGE_URL` corresponde a Tanaka y
  `SUPABASE_SERVICE_ROLE_KEY` a la clave central ya preparada en Railway.
- El copiador guarda instantáneas verificadas en un esquema privado de la misma
  base central. Copia los bytes por la API de Storage, conserva las rutas y
  comprueba tamaño, SHA-256 y tipo. Una ejecución posterior reutiliza objetos
  idénticos. Un conflicto de filas con igual identidad, un archivo faltante o una
  diferencia de permisos detiene la copia sin reemplazar la autoridad de origen.

## Barrera y recuperación

La copia final y `install` requieren el mismo corte registrado y un drenado
verificado. La comprobación operativa que alimenta ese registro aún requiere
acceso privado a los bots, al CRM comercial y al piloto. Nunca se debe rellenar
manualmente el indicador de drenado para habilitar la copia final.

Antes del corte, mantener las cuatro colas y sus consumidores existentes. Bloquear
nuevas operaciones que escriban datos, conservar la entrada firmada y dejar que
terminen los trabajos en curso, incluidos los del CRM y su piloto. La copia
final debe capturar esos cinco orígenes ya inmovilizados. `install` valida las
instantáneas, instala filas y secuencias conjuntamente y deja deshabilitados
los escritores centrales.

Los procesos centrales se configuran con el proyecto y esquema verificados,
usando sus credenciales y conexiones existentes. Conservar el mismo actor, cola,
VAPID, remitente y las protecciones vigentes. Antes de liberar las colas, comprobar
el SHA, el destino de cada escritor y /ready del CRM. Si el corte falla antes de
la primera escritura central, conservar o recuperar el origen operativo. Después
de una escritura central, recuperar desde esa autoridad y consultar los mismos
comandos pendientes, sin reintentar envíos inciertos ni restaurar una copia vieja.
