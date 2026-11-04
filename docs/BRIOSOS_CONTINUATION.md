> Histórico del piloto anterior. Para la entrega multimedia/multiempresa usar únicamente [CRM_MEDIA_DEPLOYMENT.md](CRM_MEDIA_DEPLOYMENT.md); las instrucciones de credencial personal o restricción Briosos de este documento no aplican a esa entrega.

# Continuar el piloto Briosos, sin reconstruir

El camino elegido por el usuario es ejecutar el despliegue con la IA del proyecto
Railway, usando [RAILWAY_BRIOSOS_CRM_PROMPT.md](RAILWAY_BRIOSOS_CRM_PROMPT.md).
No hace falta resolver las credenciales de esta sesión Codex para usar ese camino.
El diagnóstico de acceso que sigue es histórico, no un requisito del despliegue.

El código está terminado para la integración del piloto y publicado en Git.
La activación y la recepción física todavía no se han verificado. No confundir
pruebas con proveedores simulados con un despliegue real.

## Código y autorizaciones de la conversación

- Bot/CRM: `JeroCQ/Whatsapp-Bot`, rama existente `crm-staging-safety`.
  Base funcional/documental: `1e1e7618846a51e2537b0b9bf0027183de69c775` o descendiente.
- Frontend: `JeroCQ/chatty-catalog-compass`, `main`, commit publicado y verificado
  `6060d7828cc7ff58fb2b644911b847404a71302d` o descendiente.
- El usuario autorizó modificar y subir el frontend a main, preservar las pantallas
  actuales y activar únicamente el piloto de Briosos. Esa integración ya se subió;
  no reaplicar el parche archivado en `crm/integrations/lovable`.
- Briosos es un bot de pruebas existente, alojado en el mismo proyecto Railway que
  el CRM. Conservar ese proyecto y los servicios existentes. No crear otra rama,
  proyecto o emisor Meta por comodidad.
- No hacer push/merge a `43-CRM`, `42-actualizacion-de-precios` ni
  `crm-railway-dockerfile`. No modificar Tanaka, Memo's ni Velvet, sus servicios,
  webhooks, bases, permisos o credenciales.

## Bloqueo de esta ejecución

El usuario mostró `RAILWAY_TOKEN` guardado y posteriormente publicado desde el
panel Environment correcto. No pedir que lo cree otra vez ni que lo pegue en chat.
Sin embargo, esta ejecución continuó devolviendo siempre la misma instancia y
configuración inicial: revisión 1, sin secretos, sin variables y sin destinos
Railway permitidos. La CLI también respondió Unauthorized. Esperar y recargar no
cambiaron ese estado. No se pudo probar la validez del token ni identificar con
certeza el fallo de vinculación de la plataforma.

La nueva ejecución debe comprobar primero su configuración efectiva y acceso
Railway. Usar las credenciales por su ruta soportada; un placeholder del proxy no
es prueba de token inválido. No inspeccionar archivos de credenciales, extraer
tokens del proxy ni saltar denegaciones de red. Las herramientas de esta sesión
permiten leer estado y guardar borradores, pero no reiniciar/revincular el runtime.

CLI oficial instalada en el snapshot:
`/workspace/.onboarding/railway-cli/node_modules/.bin/railway` (5.71.0).
Si falta en otra ejecución, instalar mediante el método oficial conservando proxy,
CA y verificación TLS. Un Project Token puede carecer de operaciones de usuario
como whoami/list: verificar acceso al proyecto, no solo esos comandos.
No imprimir variables de servicios ni logs que contengan secretos.

## Secuencia pendiente

1. Leer AGENTS.md de ambos repositorios y los runbooks enlazados abajo. Comprobar
   HEAD, cambios existentes y acceso real a Railway antes de mutar servicios.
2. Identificar el proyecto y los servicios web/worker de Briosos por sus IDs y
   BUSINESS_ID; comprobar fuentes, commits y configuración. No asumir IDs ni URLs.
3. Verificar el esquema autoritativo del bot. Si falta, obtener acceso autorizado,
   backup y aplicar el upgrade revisado solo a su Supabase de pruebas. Las tablas
   SQL de control pertenecen al bot; no simular la pausa escribiendo desde CRM.
4. Configurar y desplegar solo los servicios del piloto con el código entregado:
   bot y sus workers coherentes; CRM API y sync-worker con PostgreSQL propio,
   Dockerfile CRM preservado y Alembic `0003_bot_commands`.
5. Fuente real soportada: `CRM_SOURCE_MODE=bot_api`, exportación GET del bot con un
   secreto dedicado; control por otro secreto. No copiar SERVICE_ROLE_KEY ni
   tokens Meta del bot al CRM. No hacen falta claves de los bots para autenticar
   esta ejecución en Railway. `NOTIFICATIONS_ENABLED=false`.
6. Completar la configuración privada del servidor frontend: CRM_BACKEND_URL,
   CRM_JWT_SECRET, CRM_PILOT_BUSINESS_ID=briosos, CRM_PILOT_OPERATOR_ID individual
   y CRM_PILOT_OPERATOR_SECRET personal nuevo. Ninguno debe tener prefijo VITE_.
   Activar CRM_PILOT_ENABLED solo cuando bot/API/worker estén listos. La interfaz
   ya existe en `/chats?crm_pilot=1`; las pantallas anteriores se conservan.
7. Ejecutar backfill acotado desde el worker y verificar salud, baseline, inbox,
   autorización y control. Antes de mensajes externos, identificar un número y
   destinatario de pruebas autorizados; no elegir un cliente histórico al azar.
8. Demostrar y registrar: cliente → handoff → pausa confirmada → respuesta humana
   enviada únicamente por el bot → recepción real → cierre → siguiente mensaje
   atendido por IA. accepted no equivale a delivered/read. Un unknown no se reenvía
   automáticamente. No declarar operativo hasta verificar este ciclo real.

## Evidencia existente; no repetir ni ocultar fallos

- CRM: 210 tests pasan; Ruff, formato y mypy pasan.
- Aceptación PostgreSQL desechable: 14 tests pasan, Meta/Gemini simulados. Incluye
  concurrencia, doble clic, timeout, fallos, aislamiento, esquema nuevo/upgrade y
  fallos de entrega que vuelven al inbox con la siguiente lectura incremental.
- Bot: 1004 pasan, 3 omitidos, un fallo previo en instrucciones Memo's; archivos
  comerciales intactos. No cambiarlos para ocultar ese fallo.
- Frontend real: 10 pruebas Node, 8 pruebas previas Bun, TypeScript, lint nuevo y
  build de producción pasan; manifiestos/lockfile intactos; secretos ausentes
  del bundle público. El piloto sigue desactivado por defecto.
- Uvicorn local, migraciones y /health + /ready verificados con fuentes disabled.

Contrato y operación: [CRM_PILOT_HANDOFF.md](CRM_PILOT_HANDOFF.md).
Validación backend: [PILOT_VALIDATION.md](../crm/docs/PILOT_VALIDATION.md).
En el frontend: `docs/CRM_PILOT.md` y `src/lib/crm-pilot.server.ts`.
