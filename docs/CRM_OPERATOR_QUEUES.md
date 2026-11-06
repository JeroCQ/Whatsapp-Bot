# Operadora Tanaka/Velvet y colas de Memo’s

## Contrato del prototipo

Las contraseñas actuales de Tanaka y Velvet mantienen sus perfiles y permisos de Gestión Bot separados. Solo en CRM comparten el actor `<CRM_PILOT_OPERATOR_ID>:tanaka`, autorizando también al propietario anterior `:velvet`. Los casos ya tomados conservan su propietario real y pueden responderse, cargar adjuntos o devolverse a IA sin migrar propietarios históricos. Admin mantiene el alcance global.

Humano obtiene ambas marcas en una única consulta del backend, ordenada por último mensaje real antes de paginar, con desempate negocio/teléfono. IA permite una marca; Todo ambas o una. Cada conversación lleva marca/color. Las claves son negocio + contacto; la conversación seleccionada determina siempre el bot de la acción, independientemente del filtro. El ámbito de cada negocio conserva el desmontaje y cancelación existentes al cambiar de conversación entre marcas.

`memos` se muestra como **Memo’s Detal** y `memos_mayoristas` como **Memo’s Mayoristas**. Ambos corresponden a `business_id=memos`, el mismo bot y el login existente, con actores `:memos` / `:memos_mayoristas` y colas `retail` / `wholesale`. Los JWT incluyen rol, cola, alias y destinos derivados exclusivamente del perfil autenticado por el servidor. Se aplican también a detalle, control, notas, tareas, archivos, métricas y notificaciones; elegir otro negocio o identificador en el navegador no amplía permisos.

Se reutiliza `conversation_states.customer_data`: `tipo_cliente`, `tipo_de_cliente`, `customer_type`, `client_type`, `tipoCliente` o `segment`. Valores explícitos mayorista/wholesale/B2B dirigen a Mayoristas; detal/retail/minorista a Detal. Ausente o no reconocido: Detal con **Por clasificar**. No se infiere por precio, texto o teléfono ni se cambian instrucciones comerciales. La cola y una transferencia manual persisten durante el ciclo humano; un nuevo handoff vuelve a consultar el tipo almacenado por el bot.

## Transferencia

CRM: `POST /v1/conversations/{business_id}/{phone_number}/transfer`:

```json
{"command_id":"unique-operator-command","expected_version":7,"target_queue":"wholesale"}
```

Solo Memo’s; destinos `retail`/`wholesale`. El navegador no envía actor, credencial ni URL. El CRM fija el destino y usa el endpoint autenticado del bot `POST /internal/crm/v1/businesses/{business_id}/conversations/{phone}/transfer`.

La función del bot bloquea la fila, verifica versión, propietario o espera sin asignar y ausencia de envíos `sending/unknown`; cambia propietario/versión manteniendo `is_paused=true`. No usa close/take, no crea clientes, no altera follow-ups ni elimina historial. Una carrera envío/transferencia admite una sola operación. El nuevo asesor conserva los controles de propietario, pausa y ventana Meta antes de responder.

La asignación y la cola CRM cambian solo al confirmarse la autoridad. El comando es idempotente; un timeout se recupera con GET del mismo `command_id`, sin repetir POST. Su emisor puede consultar el resultado después de cambiar de cola. PATCH de `assigned_to` no permite saltarse la transferencia.

## Destinatarios e incorporación

Se reutilizan agentes, preferencias, asignaciones, suscripciones y outbox actuales. La operadora recibe sus casos Tanaka/Velvet; cada acceso Memo’s, únicamente su cola/casos. La transferencia invalida avisos pendientes anteriores y crea un evento para el destinatario. Se verifica transición/asignación justo antes de enviar, coordinando bloqueos con la transferencia. Admin queda sin alertas por defecto y puede usar las preferencias explícitas por negocio.

La incorporación no genera eventos de handoffs históricos. Se desactivan las suscripciones de los perfiles legados del mismo operador; el registro existente vuelve a vincular el dispositivo al actor canónico. La carga Push conserva el perfil de login para que el service worker siga rechazando avisos de otra sesión. Una contraseña compartida no identifica individualmente a una empleada.

## Únicos pasos manuales nuevos

1. **Hosting frontend/Lovable:** añadir `PASSWORD_MEMOS_MAYORISTAS` como secreto privado con una contraseña diferenciada. Conservar todas las contraseñas actuales, `CRM_PILOT_OPERATOR_ID`, JWT, VAPID y demás variables. Sin prefijo `VITE_` ni secretos en Git.
2. **Supabase del bot de Memo’s:** revisar y ejecutar [supabase/upgrade_crm_operator_transfer.sql](../supabase/upgrade_crm_operator_transfer.sql), después del respaldo. Es la base que usa `Memos-Whatsapp-Bot`, con `public.conversation_states`; **no** PostgreSQL CRM ni los Supabase de Tanaka/Velvet/Briosos. Requiere la instalación CRM previa; reemplaza únicamente la función de control y conserva permisos. La definición coincide con `bootstrap.sql`, `upgrade_existing_brand.sql` y `upgrade_crm_bot_commands.sql`. No hay migración Alembic nueva.
3. **Actualización manual coordinada:** SQL Memo’s → bot Memo’s existente con el commit de esta entrega → API y sync CRM con el mismo commit → frontend con su PR. Conservar roots, arranques, URLs, números, Redis y secretos actuales. El par citado en esta conversación es `crm-staging-api` / `crm-staging-sync`: actualizar el que ya consume la app, sin crear servicios. Los alias compartidos no requieren cambios de configuración/envío en los bots de Tanaka o Velvet.
4. Recargar una vez cada acceso para registrar ámbitos y preferencias y renovar las suscripciones existentes. Web Push sigue necesitando la configuración y permiso actuales del navegador. No se habilitan flags ni se aprovisionan claves automáticamente; no se envía backlog.

No se ejecutaron migraciones productivas, merges, despliegues, cambios reales de secretos ni envíos a clientes. Las comprobaciones usan PostgreSQL desechable, proveedores simulados y navegador interceptado. Falta la prueba física posterior al despliegue manual. Para retirar el prototipo, conservar esta atención hasta transferir explícitamente los casos Mayoristas a Detal; no liberar casos a IA ni borrar asignaciones como rollback.
