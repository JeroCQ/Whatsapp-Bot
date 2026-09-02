# Puesta en producción de Velvet con recursos nuevos

> El proceso general y las correcciones aprendidas aquí se consolidaron en
> [`docs/NEW_BUSINESS_SETUP.md`](docs/NEW_BUSINESS_SETUP.md), el runbook canónico
> para toda marca nueva. Este documento conserva únicamente el contexto de Velvet.

Velvet se despliega desde este repositorio como una instancia aislada con
`BUSINESS_ID=velvet`. Puede compartir la instalación raíz de Chatwoot y, cuando el
equipo lo decida conscientemente, credenciales de Gemini/GitHub; no debe compartir
número, Supabase, Redis/cola, cuenta/inbox de Chatwoot ni secretos de otra marca.

## Orden seguro de implementación

1. **Registrar el estado actual.** No cambies Tanaka. Reserva el dominio Railway de
   Velvet y genera secretos nuevos para Meta, dashboard y Chatwoot.
2. **Crear Supabase.** Crea un proyecto vacío para Velvet y ejecuta
   `supabase/bootstrap.sql` en SQL Editor. Comprueba las cuatro tablas, índices y el
   bucket público `catalogos`. Guarda la URL y `service_role`; nunca uses la anon key
   en el bot ni expongas `service_role` al navegador. **No ejecutes `supabase/upgrade_existing_tanaka.sql`:
   ese archivo es únicamente para actualizar
   el Supabase histórico de Tanaka sin borrar sus datos.** `bootstrap.sql` ya trae
   las mismas funciones actuales de Tanaka: idempotencia/cola, vínculo Chatwoot,
   cancelación de follow-ups, memoria del cliente y pedido, catálogo y allow-list
   administrativa protegida. La consulta final debe devolver `true` en todo.
3. **Preparar Meta y el número.** Crea una app Meta nueva o un entorno empresarial
   inequívocamente aislado, añade el producto WhatsApp, incorpora y verifica el
   número nuevo, registra su Phone Number ID y crea un token permanente de system
   user. Genera un `WA_VERIFY_TOKEN` nuevo. No conectes todavía el callback.
4. **Crear Chatwoot.** En la instalación correcta crea una cuenta/workspace
   **Velvet**, agentes propios y un **API inbox** exclusivo. Registra account ID,
   inbox ID y un API token con acceso a esa cuenta. No uses el inbox nativo de
   WhatsApp: Meta debe entregar primero al bot.
5. **Crear Railway.** Crea un proyecto nuevo desde este repositorio, no clones un
   servicio que arrastre secretos. Añade Redis nuevo, configura todas las variables
   de abajo, despliega y genera el dominio público.
6. **Validar Railway antes de conectar webhooks.** Abre `/`, confirma `status: ok`,
   cola habilitada y al menos un worker. Revisa que los logs indiquen
   `whatsapp-events-velvet` y el commit esperado.
7. **Conectar Meta.** Configura `https://DOMINIO-VELVET/webhook` con el mismo
   `WA_VERIFY_TOKEN`, valida el callback y suscribe `messages`.
8. **Conectar Chatwoot.** En la configuración del **API inbox Velvet**, fija su
   webhook URL como `https://DOMINIO-VELVET/chatwoot-webhook` y copia el `secret`
   de ese canal a `CHATWOOT_API_INBOX_WEBHOOK_SECRET`. Este callback es el canal de
   entrega de respuestas del asesor; si falla, Chatwoot muestra “Failed to send”.
   No lo confundas con el HMAC token de identidad. Un account webhook adicional es
   opcional y usa su propio `CHATWOOT_WEBHOOK_SECRET`.
   Un administrador puede verificar el valor correcto consultando el inbox por
   `GET /api/v1/accounts/{account_id}/inboxes/{inbox_id}`: usa el campo `secret`,
   no `hmac_token`, y nunca lo pegues en tickets o logs.
9. **Cargar catálogo y probar.** Sube la imagen oficial como un único objeto
   `catalogos/velvet.png` desde el dashboard. Prueba saludo de Camila, catálogo,
   pedido, comprobante, handoff, reactivación desde Chatwoot y aislamiento respecto
   de Tanaka antes de anunciar el número.

## Variables de Railway Velvet

| Variable | Valor/origen |
| --- | --- |
| `BUSINESS_ID` | `velvet` |
| `SUPABASE_URL` | URL del Supabase nuevo Velvet |
| `SUPABASE_SERVICE_ROLE_KEY` | `service_role` del Supabase Velvet |
| `WA_PHONE_NUMBER_ID` | ID del número nuevo Velvet |
| `WA_TOKEN` | Token permanente nuevo |
| `WA_VERIFY_TOKEN` | Secreto nuevo, igual al callback Meta |
| `GEMINI_API_KEY` | Clave válida; separada si se desea aislar cuota |
| `REDIS_URL` | Referencia al Redis nuevo, normalmente `${{Redis.REDIS_URL}}` |
| `QUEUE_NAME` | `whatsapp-events-velvet` |
| `CHATWOOT_BASE_URL` | Raíz HTTPS, sin `/app` ni `/api/v1` |
| `CHATWOOT_ACCOUNT_ID` | Cuenta Velvet |
| `CHATWOOT_INBOX_ID` | API inbox Velvet |
| `CHATWOOT_API_TOKEN` | Token del agente/integración Velvet |
| `CHATWOOT_WEBHOOK_SECRET` | Opcional: secreto de un account webhook Velvet separado |
| `CHATWOOT_API_INBOX_WEBHOOK_SECRET` | `secret` firmante del API inbox Velvet; requerido para respuestas de agentes |
| `CHATWOOT_ASSIGNMENT_MODE` | `automatic` (o `fixed` junto con un `CHATWOOT_ASSIGNEE_ID` válido) |
| `GITHUB_SI_PATH` | `src/clients/velvet/system_instruction.txt` (o eliminar para usar la ruta derivada) |
| `CATALOG_STORAGE_BUCKET` | `catalogos` |
| `DASHBOARD_API_KEY` | Secreto fuerte exclusivo de Velvet, si se usa dashboard |
| `PRESAVED_FILES_JSON` | JSON exacto mostrado debajo |

```json
[{"id":"catalogo_pdf","description":"Catálogo oficial de Velvet Repostería y Mochis Velvet; enviarlo cuando pregunten por productos, sabores, cantidades, precios u opciones.","type":"image","filename":"Catálogo Velvet.png","caption":"Con mucho gusto, te comparto nuestro catálogo ☺️"}]
```

En el editor de variables de Railway pega **solo el JSON anterior**, desde `[` hasta
`]`: Railway guarda el valor directamente y no necesita las comillas exteriores ni
las barras `\`. Si se define desde Bash o un archivo `.env`, la forma equivalente es:

```bash
PRESAVED_FILES_JSON='[{"id":"catalogo_pdf","description":"Catálogo oficial de Velvet Repostería y Mochis Velvet; enviarlo cuando pregunten por productos, sabores, cantidades, precios u opciones.","type":"image","filename":"Catálogo Velvet.png","caption":"Con mucho gusto, te comparto nuestro catálogo ☺️"}]'
```

Esta variable se configura únicamente en el Railway Velvet. No edites el valor del
Railway Tanaka que ya está live.

No configures `RUN_WORKER_IN_WEB=false` si no existe un worker separado. No copies
`catalogo_tanaka`, `catalogo_memos`, Redis, IDs, tokens, cuentas o webhooks ajenos.

## Catálogo extraído y datos aún pendientes

La pieza recibida confirma ocho sabores de Mochi, los rangos mayoristas de 48 y 96
unidades, conservación congelada y el teléfono 318 762 2894. **No muestra precio al
detal, catálogo/precios de Pastelería, tarifas de domicilio ni cuentas de pago.** El
prompt obliga a verificar esos datos; no se sustituyeron con datos genéricos ni con
tarifas o cuentas de Tanaka.

Cuando exista una pieza nueva, reemplaza el objeto `catalogos/velvet.*` desde el
dashboard y conserva solo una extensión vigente. Si Pastelería y Mochis requieren
archivos distintos, primero amplía explícitamente `PRESAVED_FILES_JSON` con IDs y
URLs separados; no reutilices `catalogo_pdf` para dos archivos simultáneos.

## Dashboard opcional

En el proxy server-side crea `VELVET_DASHBOARD_BACKEND_URL`,
`VELVET_DASHBOARD_API_KEY` y `VELVET_DASHBOARD_PASSWORD`. Fija siempre
`client_name=velvet` en servidor y envía `X-Dashboard-API-Key`; rechaza cruces con
Tanaka o Memo's. Ningún secreto debe llamarse `VITE_*` ni llegar al bundle.

## Prueba final y reversa

Confirma que el número nuevo recibe y responde; “precio” adjunta la imagen Velvet;
un pedido mayorista, una tarifa desconocida, un pago o una queja crea conversación
en el inbox Velvet; resolverla reactiva solo ese teléfono. Verifica en Supabase que
los mensajes nuevos estén únicamente en Velvet. Si falla, desconecta solo el
callback Meta de Velvet o revierte su último deploy; no modifiques Tanaka.

## Corrección para un Supabase Velvet ya creado

Si el log muestra `message_logs_role_check` al guardar `Archivos enviados`, ejecuta
una vez `supabase/enable_runtime_message_roles.sql` en SQL Editor. Los primeros
bootstraps solo aceptaban `user` y `model`, pero el runtime también necesita
`system` para recordar que ya envió el catálogo y `asesor` para el historial de
Chatwoot. Sin esta corrección el marcador falla y el catálogo vuelve a enviarse.
Las mismas sentencias ya están integradas en el SQL principal
`supabase/bootstrap.sql`, por lo que una marca creada desde cero no necesita
ejecutar después el archivo de reparación.
