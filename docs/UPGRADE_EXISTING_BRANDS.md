# Actualizar Tanaka o Memo's sin recrear sus proyectos

Este procedimiento sirve para desplegar una versión nueva del código en una marca
existente. No crea otra marca y no intercambia recursos entre Railway projects.

## 1. Registrar y respaldar

1. Anota commit, dominio, variables y estado de los webhooks actuales.
2. Crea un backup/snapshot del Supabase de esa marca.
3. No ejecutes `supabase/bootstrap.sql`: ese archivo es solo para proyectos vacíos.

## 2. Actualizar el Supabase existente

1. Abre `supabase/upgrade_existing_brand.sql`.
2. Ejecuta primero solo la consulta inicial de `chatwoot_conversation_id`
   duplicados. Debe devolver cero filas; el propio SQL también aborta si encuentra
   duplicados.
3. Ejecuta el archivo completo y exige `true` en todas las columnas de la consulta
   final. Es idempotente y conserva clientes, conversaciones y mensajes.
4. No ejecutes además `enable_runtime_message_roles.sql`: ya está incluido en el
   upgrade completo. Ese archivo pequeño queda para reparar únicamente el error de
   roles sin aplicar todavía el resto del upgrade.

## 3. Mantener la identidad del Railway

No copies el bloque completo de variables de otra marca. Conserva las credenciales
existentes y verifica solamente su correspondencia:

| Variable | Tanaka | Memo's |
| --- | --- | --- |
| `BUSINESS_ID` | `tanaka` | `memos` |
| `QUEUE_NAME` | `whatsapp-events-tanaka` | `whatsapp-events-memos` |
| `REDIS_URL` | Redis del proyecto Tanaka | Redis del proyecto Memo's |
| `GITHUB_SI_PATH` | `src/clients/tanaka/system_instruction.txt` o sin definir | `src/clients/memos/system_instruction.txt` o sin definir |
| `SUPABASE_URL` / `SUPABASE_SERVICE_ROLE_KEY` | Proyecto Tanaka | Proyecto Memo's |
| Meta y Chatwoot | IDs/tokens de Tanaka | IDs/tokens de Memo's |

Conserva el `PRESAVED_FILES_JSON` propio y el objeto `catalogos/{BUSINESS_ID}.*` de
cada Supabase. No copies la imagen Velvet ni cambies el catálogo vigente.

En cada API inbox, configura su webhook URL hacia `/chatwoot-webhook` del Railway
de esa misma marca y guarda el `secret` del canal como
`CHATWOOT_API_INBOX_WEBHOOK_SECRET`. Es distinto del HMAC token de identidad y del
secreto de un account webhook. Mantener `CHATWOOT_ASSIGNMENT_MODE=automatic` y
`CHATWOOT_ASSIGNEE_ID` vacío es válido; la entrega no depende del modo de asignación.
Un administrador puede confirmar el secreto con
`GET /api/v1/accounts/{account_id}/inboxes/{inbox_id}` y tomando el campo `secret`
(nunca `hmac_token`). Después de entregar a Meta, el bot actualiza ese mensaje del
API inbox a `delivered`; si Meta falla, lo marca `failed` con un error genérico.

## 4. Desplegar y verificar

1. Despliega primero sin cambiar callbacks ni secretos.
2. Abre `/` y confirma el commit, `queue.enabled=true`, el nombre de cola correcto y
   al menos un worker.
3. Envía una conversación nueva y verifica en el Supabase de esa misma marca filas
   nuevas en `customers`, `conversation_states` y `message_logs`.
4. Pide el catálogo: debe enviarse una sola vez y debe poder guardarse un log con
   `role=system` sin error `message_logs_role_check`.
5. Fuerza un handoff y confirma que solo aparece en la cuenta/inbox Chatwoot de esa
   marca; resuelve el ticket y confirma la reactivación.
6. No pruebes Tanaka y Memo's con la misma cola Redis ni apuntando ambos números al
   mismo callback.

## 5. Rollback

Si falla, revierte únicamente el Railway de esa marca al commit anterior. No reviertas
el SQL eliminando columnas o roles: las adiciones son compatibles con la versión
anterior. Si fuera necesario, desconecta temporalmente solo su callback Meta. No
modifiques el Railway, Supabase, Redis, número o Chatwoot de las otras marcas.
