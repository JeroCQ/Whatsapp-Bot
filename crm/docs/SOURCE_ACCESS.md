# Acceso de fuentes: permisos efectivos, no nombres de variables

## Estado de esta entrega

**La activación con fuentes productivas está bloqueada.** El worker de staging
acepta solamente fuentes PostgreSQL sintéticas en loopback, con base llamada
`crm_test_source_*` o `crm_staging_source_*` y un login dedicado
`crm_staging_reader_*`. Rechaza destinos remotos y nombres fuera de ese espacio
antes de abrir una conexión. No hay un flag soportado que convierta este ensayo
en autorización para leer Supabase reales.

`<BUSINESS>_SOURCE_READONLY_DATABASE_URL` es una credencial PostgreSQL de lector,
no una `SERVICE_ROLE_KEY` renombrada. La API no necesita esta variable. Cada empresa
usa otra base/fachada y otro lector. Las claves administrativas de los bots no se
copian ni se reutilizan. El adaptador HTTP heredado con bloqueo GET/HEAD no prueba
la limitación de una credencial privilegiada y no habilita este modo productivo.

## Por qué un rol NOINHERIT no soluciona por sí solo el problema

Todo login PostgreSQL recibe los permisos de `PUBLIC`. `NOINHERIT` no los elimina
y una membresía puede permitir `SET ROLE` aun sin herencia automática. Si PUBLIC
conserva `TEMPORARY`/`CREATE`, un SELECT ajeno o EXECUTE sobre una función que cambia
datos, no existe un `DENY` individual que revoque solamente al lector ese acceso.
Un propietario de tabla o un rol `BYPASSRLS` tampoco queda protegido por una
política RLS ordinaria. Un tipo custom puede invocar sus funciones de salida o
conversión de forma implícita.

Por ello no se aplica una receta de permisos a la base de un bot ya operativo:
revocar ACL compartidas, funciones o cambiar sus políticas podría afectar a ese
bot. Las migraciones CRM no contienen ningún cambio de roles/políticas de origen.
No se usa `default_transaction_read_only` como mecanismo de autorización. Las
pruebas ejecutan con esa variable en `off` y aun así los permisos deniegan DML.

## Propuesta SQL para revisión separada

`security/isolated_source_proposal.sql` es una **propuesta para una fachada de
pruebas nueva**, no una migración del bot ni un script para Supabase existentes.
Exige el prefijo sintético de base y el marker
`public.crm_synthetic_source_marker(marker='crm-synthetic-only-v1')`. Solo acepta
un nuevo lector con prefijo propio; la repetición exige un COMMENT que identifica
ese rol como propiedad de la misma fachada. No modifica roles ajenos.

La propuesta:

- Crea un login separado sin superusuario, creación de bases/roles, herencia,
  replicación o bypass de RLS; limita ese login a una conexión.
- En **esa base aislada** revoca CREATE/TEMPORARY heredados de PUBLIC y CREATE en
  esquemas, y elimina grants previos del lector sobre tablas y secuencias.
- Revoca EXECUTE de PUBLIC/lector para rutinas no builtin, agregados de usuario y
  funciones volátiles, incluidas las de `pg_catalog` como `pg_notify`. Requiere
  permisos administrativos suficientes para ajustar esas ACL en la base nueva.
- Otorga USAGE de `public` y SELECT **por columnas** solamente para los campos
  conocidos de `customers`, `conversation_states`, `message_logs` y
  `handoff_deliveries`. No otorga SELECT de `*`, secuencias u otras tablas.
- Solo permite tablas base independientes, sin particiones/herencia. Los campos
  leídos deben usar tipos builtin concretos de `pg_catalog`; rechaza dominios,
  enums, arrays y tipos custom. Una fachada real tendrá que normalizarlos fuera
  del CRM mediante un diseño revisado, no ocultarlos tras una función privilegiada.
- Habilita y fuerza RLS, con una política SELECT para el lector de esa fachada.
  No crea políticas de escritura.

El procedimiento sintético reproducible crea las tablas y el marker antes de
aplicar esta propuesta. Como forma de revisión manual, en una base sintética ya
creada y utilizando la cuenta administrativa **de pruebas**:

```bash
# SYNTHETIC_OWNER_DSN usa el formato psql postgresql:// y apunta solo a la base nueva.
psql "$SYNTHETIC_OWNER_DSN" -v ON_ERROR_STOP=1 \
  -c "SET crm.reader_role='crm_staging_reader_review_example'" \
  -f security/isolated_source_proposal.sql
```

El cargador sintético genera y configura la contraseña del lector local sin
imprimirla y escribe las variables de staging en un archivo con permisos 0600
fuera del checkout. El SQL no contiene contraseñas. El DBA debe revisar por
separado cualquier futura propuesta de acceso real; este script la rechaza.

## Verificación al conectar

`PostgresSourceAdapter` abre una sola conexión de forma perezosa. Antes de leer
datos, una consulta de catálogo comprueba privilegios **efectivos**, incluyendo
los obtenidos a través de PUBLIC:

- Flags elevados, membresías, propiedad de relaciones y CREATE/TEMP efectivos.
- Escritura por tabla o por columna, lectura de relaciones ajenas y privilegios
  sobre secuencias.
- EXECUTE de funciones/procedimientos/agregados no builtin o volátiles.
- RLS y FORCE RLS en cada tabla, ausencia de herencia y tipos de columna seguros.
- SELECT de cada columna permitida y ausencia de lectura de columnas adicionales.

Después construye SELECT parametrizados desde una lista cerrada de tablas y
columnas. No acepta SQL del llamador ni RPC. Los límites de ejecución incluyen
la auditoría, los SELECT y los `SET statement_timeout` que ajustan el timeout al
presupuesto restante. Ese SET controla duración; no proporciona autorización de
lectura. Si una política RLS intenta llamar a una función sin EXECUTE, PostgreSQL
deniega la consulta y la sincronización falla de forma segura.

La auditoría es defensa adicional: la autorización real reside en las ACL y RLS
de la base. Un administrador puede cambiar esas ACL posteriormente; este ensayo
no garantiza seguridad frente al administrador de la fuente ni sustituye revisión
de configuración, cambios de permisos, credenciales y conectividad.

## Evidencia de pruebas desechables

`tests/test_source_permissions.py` crea y elimina sus propias bases y roles de
pruebas. Verifica lectura permitida y denegación real de INSERT, UPDATE, DELETE,
TRUNCATE, CREATE de tabla, TEMP, columnas extra, función mutadora y `pg_notify`.
También comprueba:

- RLS que filtra filas efectivamente; ausencia de FORCE RLS y ownership bloqueados.
- Grants peligrosos de PUBLIC, incluso UPDATE de una sola columna.
- Membresía con INHERIT FALSE y SET TRUE detectada, aunque el privilegio efectivo
  inicial no muestre UPDATE.
- Flags elevados, función SECURITY DEFINER/STABLE llamada por una política RLS,
  función volátil de política y agregado de usuario invocable por PUBLIC.
- Rechazo de tipos custom antes de materializar valores y paginación legacy.
- Conteo de consultas/auditoría y una sola conexión por instancia.
- Rechazo de destinos productivos/administrativos antes de `psycopg.connect`.

No se ejecutan ataques de escritura contra producción. Ningún rol, política,
función, tabla o secreto real de los bots se alteró para realizar estas pruebas.

## Requisitos antes de una fuente real

Se necesita un diseño aprobado que entregue a cada empresa una fuente con
credenciales dedicadas y permisos efectivos demostrablemente limitados. Una
fachada o réplica aislada debe definir quién la alimenta, qué datos contiene,
latencia/retención y cómo se evita modificar la base de los bots. No se proporciona
ni se activa aquí esa infraestructura o un proceso de copia productiva.

Si el proveedor no permite limitar PUBLIC/rutinas y demostrar RLS/ownership sin
afectar el bot, **no se puede usar su base actual con este modo**. Mantener staging
con fuentes sintéticas. Cualquier primera lectura real requiere revisión de DBA,
rotación y revocación de credenciales independientes, conectividad aprobada,
validación de permisos y un cambio explícito del bloqueo productivo del runtime.
