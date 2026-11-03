# Desplegar el piloto Briosos desde Railway

Este es el encargo para la IA de Railway dentro del proyecto existente que contiene
el bot de pruebas Briosos y el CRM. El código ya está implementado y publicado.
Ejecuta configuración, migraciones autorizadas y despliegue; no reconstruyas la
aplicación ni solicites desarrollo adicional a Lovable.

## Alcance autorizado y código

- Único bot autorizado: **Briosos de pruebas**. Identifica primero sus servicios
  y BUSINESS_ID; no asumas IDs a partir del nombre del proyecto.
- Reutiliza los servicios web/worker de Briosos, API/worker CRM y PostgreSQL CRM
  existentes. Conserva datos, instrucciones, catálogos, dominio, webhook Meta,
  número y cola actuales de Briosos. No crees otra rama, proyecto o emisor Meta.
- No modifiques Tanaka, Memo's ni Velvet, sus servicios, fuentes o credenciales.
- No hagas push/merge a `43-CRM`, `42-actualizacion-de-precios` ni
  `crm-railway-dockerfile`. Cambiar la Source branch del servicio piloto es distinto
  de modificar esas ramas: solo Briosos debe apuntar a `crm-staging-safety`.
- Bot/CRM: `JeroCQ/Whatsapp-Bot`, rama **crm-staging-safety**, HEAD actual.
  Incluye `bdd1c00c439cd0ff75f345ba20551a688965a350` y sus descendientes.
- Frontend ya integrado: `JeroCQ/chatty-catalog-compass`, **main**,
  `6060d7828cc7ff58fb2b644911b847404a71302d` o descendiente compatible. No reapliques
  el parche archivado ni reimplementes sus botones/compositor.
- Lee AGENTS.md y `docs/CRM_PILOT_HANDOFF.md`. El usuario ya autorizó este piloto y
  la integración frontend. Ejecuta el trabajo disponible, sin pedir otra
  confirmación genérica. Pide solo un dato/acceso externo concreto si falta.

## 1. Verificar y preparar el esquema antes de activar

Inventaría los servicios y registra sus IDs, ramas, commits y configuraciones
actuales sin imprimir secretos. Confirma que el Supabase seleccionado pertenece
exclusivamente a Briosos. Realiza backup con un acceso autorizado y revisa el SQL.

Para el bot existente, usa `supabase/upgrade_existing_brand.sql`; no uses bootstrap
sobre su base actual. Verifica el bloque CRM CONTROL, las tablas
crm_control_commands/crm_send_attempts y los RPC crm_control_view/crm_control_dispatch.
Ambos SQL canónicos tienen paridad para instalaciones nuevas/actualizaciones.
Comprueba que PostgREST reconoce los RPC tras la recarga de su schema cache.

Una SERVICE_ROLE_KEY de Supabase **no es acceso SQL/DDL**. Tener acceso a Railway
no implica poder migrar Supabase. Si falta ese acceso, entrega una única acción
precisa al propietario: ejecutar el archivo revisado en el SQL Editor del
Supabase Briosos después del backup. No intentes sortear permisos y no actives
HUMAN_SUPPORT_PROVIDER=crm antes de verificar el esquema.

## 2. Configurar y desplegar el bot piloto

Source: rama crm-staging-safety. Root Directory: `/`. Conserva `railway.json` y el
arranque existente `python run_railway.py`/startCommand del archivo. Si hay worker
bot separado, despliega la misma versión y variables en él; no dejes workers
antiguos consumiendo la misma cola. Drena los trabajos antiguos de forma controlada,
sin borrar historiales ni conversaciones.

Conserva las credenciales Supabase/Meta/Gemini y la cola de Briosos exclusivamente
en el bot. Cuando esquema y configuración estén listos:

```dotenv
BUSINESS_ID=briosos
HUMAN_SUPPORT_PROVIDER=crm
CRM_CONTROL_ENABLED=true
CRM_BOT_CONTROL_SECRET=<secreto-control-dedicado>
CRM_SOURCE_READ_ENABLED=true
CRM_SOURCE_READ_SECRET=<secreto-lectura-distinto>
WA_APP_SECRET=<app-secret-real-de-la-aplicacion-Meta-del-piloto>
```

Genera secretos dedicados aleatorios >=32 caracteres solo si faltan; conserva los
existentes válidos para no romper referencias. Control y lectura son diferentes.
WA_APP_SECRET debe ser el secreto real de firma de la app Meta; no lo inventes,
no uses WA_TOKEN en su lugar. Si falta, pide únicamente cargar ese secreto en la
configuración privada. No lo imprimas en el informe ni en chat.

En proveedor crm no se usa Chatwoot. No crees tickets ni cambies recursos Chatwoot
de otros negocios. El bot sigue siendo autoridad y único emisor Meta.

## 3. Configurar y desplegar CRM API y sync-worker

Ambos usan esta misma rama, Root Directory `/crm` y `crm/Dockerfile`, que ya conserva
el arreglo compatible con Railway. El DockerfilePath relativo al root es
`Dockerfile`, no el Dockerfile raíz del bot.

- API: configuración versionada `crm/railway.toml`, preDeploy `alembic upgrade head`,
  start de Uvicorn del archivo y healthcheck `/ready`.
- Worker: `crm/railway.worker.toml`, start `python -m crm_service.worker`. Inícialo
  después de migrar la base CRM a `0003_bot_commands`.
- Reutiliza PostgreSQL propio del CRM. CRM_DATABASE_URL requiere esquema
  `postgresql+psycopg://`; adapta de forma privada la URL/reference de Railway
  conservando sus credenciales codificadas, sin mostrarlas ni usar un Supabase del bot.
- El CRM no necesita Redis. Conserva el Redis del bot si ya existe.

Configuración común de API/worker del piloto:

```dotenv
CRM_DATABASE_URL=<PostgreSQL-propio-CRM-con-esquema-postgresql+psycopg>
CRM_JWT_SECRET=<secreto-privado-compartido-solo-con-servidor-frontend>
CRM_SYNC_SECRET=<otro-secreto-privado-distinto>
CRM_BUSINESSES=briosos
CRM_SOURCE_MODE=bot_api
CRM_BOT_TEST_MODE=false
BRIOSOS_BOT_API_URL=https://<origen-real-del-bot-Briosos>
BRIOSOS_HUMAN_CONTROL_ENABLED=true
BRIOSOS_BOT_API_ENABLED=true
NOTIFICATIONS_ENABLED=false
```

Solo API: BRIOSOS_BOT_CONTROL_SECRET referencia el CRM_BOT_CONTROL_SECRET del bot.
Solo worker: BRIOSOS_BOT_SOURCE_SECRET referencia su CRM_SOURCE_READ_SECRET.
Usa referencias privadas de Railway donde sea posible; no expongas valores.
JWT y sync son distintos y de >=32 caracteres. No añadas claves administrativas
Supabase ni tokens Meta a CRM API/worker; este modo no los necesita.

Las URLs de bot y CRM son orígenes HTTPS reales sin path, usuario, query o fragmento.
No actives CRM_BOT_TEST_MODE para saltar validación con una URL HTTP privada.
Conserva los límites SYNC_* validados de `.env.example`/runbook y las notificaciones
apagadas. Si un servicio CRM ya atiende otros entornos, verifica aislamiento antes
de reemplazar su configuración global: esta autorización cubre solo el piloto.

Ejecuta desde el worker, con su configuración segura:

```bash
python -m crm_service.worker --once --business briosos
```

Repite pasos acotados hasta baseline completo. Un resultado partial continúa cursores;
no equivale a backfill terminado. Verifica /health, /ready, capabilities y sync-status
con autenticación y scope correctos. La API no debe recibir secretos de lectura para
solicitar un backfill: /internal/sync persiste trabajo que ejecuta el worker.

## 4. Lovable: únicamente configurar secretos y publicar el código existente

El frontend main ya contiene inbox, proxy JWT, tomar control, compositor, recibos
y cierre. No gastes créditos pidiendo otra implementación o una nueva interfaz.
Verifica que el build publicado contenga 6060d78 o su descendiente compatible.

Configuración **privada del servidor frontend**, sin prefijos VITE_:

```dotenv
CRM_BACKEND_URL=https://<origen-real-de-CRM-API>
CRM_JWT_SECRET=<mismo-secreto-privado-de-CRM-API>
CRM_PILOT_BUSINESS_ID=briosos
CRM_PILOT_OPERATOR_ID=<identificador-estable-de-la-persona-de-pruebas>
CRM_PILOT_OPERATOR_SECRET=<credencial-personal-nueva-aleatoria-minimo-32>
CRM_PILOT_ENABLED=true
```

Activa ese flag solo tras comprobar bot/API/worker. La credencial personal debe
pertenecer a una persona y ser distinta de JWT y PASSWORD_*; no uses una marca o
admin compartido como identidad individual. No cambies las contraseñas actuales.

Railway no concede acceso automático a los ajustes/publicación de Lovable. Si no
puedes configurarlos, termina todo lo independiente y devuelve únicamente la lista
exacta de campos privados que el propietario debe cargar/publicar, indicando dónde
copiar el JWT desde Railway sin imprimir su valor. No solicites una nueva tarea de
programación a Lovable. Consulta `docs/CRM_PILOT.md` del frontend. El panel está en
`/chats?crm_pilot=1`.

## 5. Verificación real y entrega

Pide al usuario un mensaje entrante nuevo desde su WhatsApp de pruebas autorizado
para abrir la ventana real. No selecciones un cliente histórico ni fabriques un
callback para declarar recepción física. Comprueba:

cliente → handoff guardado → pausa confirmada → tomar control → una respuesta
humana enviada por el bot → recepción en el dispositivo y recibo Meta → cerrar
y devolver IA → siguiente mensaje entrante obtiene respuesta automática.

No habilites el compositor antes de confirmar pausa/propietario/ventana. Un envío
accepted no es delivered/read. Un resultado unknown se consulta por el mismo
command_id y nunca se reenvía automáticamente. Los dobles clics conservan clave y
cuerpo originales. No reviertas a un bot antiguo con casos humanos o envíos inciertos
abiertos. No envíes mensajes externos adicionales para probar fallos.

Entrega el estado exacto: servicios cambiados, commits realmente desplegados, URLs,
migraciones aplicadas, salud/backfill, prueba física realizada y cualquier requisito
externo pendiente. No declares el piloto operativo si falta configurar el frontend
o comprobar el ciclo real. No publiques secretos ni afirmes que los otros negocios
fueron migrados.
