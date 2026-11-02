# Conversaciones que salen de una vista y vuelven a aparecer

## Evidencia revisada de Memo's

El archivo `logs.1792603560674.log.txt` contiene 1.000 registros del 21 de octubre
de 2026, aproximadamente **10:14–12:25 en America/Bogota**. No cubre días completos
ni contiene la auditoría interna de Chatwoot o los filtros de la asesora.
Los 40 registros de arranque identifican el código **e283a74**; no son evidencia
de que los cambios de esta rama estén desplegados.

- Los 18 eventos Chatwoot procesados son `message_created` y muestran `open` en
  las conversaciones **431, 433 y 435**. Esto comprueba el estado en esos eventos,
  no que nunca hayan cambiado de estado entre ellos.
- Las conversaciones **433** y **435** se crearon a las **11:10:22** y **11:35:09**,
  respectivamente, en cuenta **2**, inbox **1**. Se usó asignación `automatic`:
  el bot dejó la selección del asesor a Chatwoot. La respuesta no devolvió un
  assignee; eso no prueba que el caso estuviera sin asignar.
- Se registran cinco entregas de mensajes de la asesora hacia WhatsApp, con
  respuestas HTTP 200 de Meta y actualización de entrega en Chatwoot.
- Hay **10 respuestas 403** en `/chatwoot-webhook`, agrupadas a las **11:16** y
  **11:32**. La ruta rechaza con 403 un scope de cuenta/inbox distinto o ausente,
  después de comprobar la firma. No es el error 401 de firma inválida. El log
  anterior no incluye el evento rechazado ni sus IDs, por lo que no permite saber
  si eran callbacks de otro inbox, eventos sin inbox u otra configuración.
- No hay eventos de resolución/reapertura aceptados en este archivo. Los rechazos
  y la cobertura limitada impiden concluir que tales eventos nunca ocurrieron.
  Las etiquetas `[err]` acompañadas de `INFO` y HTTP 200/201 no prueban fallos.

No se encontraron llamadas del bot para borrar conversaciones remotas, cambiar
su asignación, resolverlas o posponerlas. El borrado de `/reset` afecta al historial
local de Supabase; no elimina un ticket de Chatwoot. Una resolución recibida de
Chatwoot desvincula el caso del bot, conserva su historial y permite una futura
conversación nueva. No se atribuye automáticamente ese cierre a un asesor:
también puede proceder de automatizaciones de Chatwoot.

## Corrección preventiva incluida en la misma revisión

Las notas internas y sus adjuntos se enviaban como `message_type=incoming` con
`private=true`. El callback de Chatwoot que reabre casos comprueba `incoming` sin
excluir notas privadas. Una nota interna retrasada podía, por tanto, reabrir un
caso resuelto o pospuesto sin que hubiera un mensaje nuevo del cliente.

Ahora las notas internas usan **`outgoing` y `private=true`**. Los mensajes reales
del cliente conservan **`incoming` y `private=false`**, con su reapertura normal.
El webhook ya excluye las notas privadas del envío a WhatsApp. No se cambian
filtros, asignaciones, estados ni credenciales de producción. La corrección es
genérica para instalaciones nuevas y existentes, sin nuevas migraciones SQL.

Referencia del contrato revisado:
[Message#reopen_conversation, Chatwoot v4.16.2](https://github.com/chatwoot/chatwoot/blob/v4.16.2/app/models/message.rb).
La versión del servidor de producción no se identifica en el archivo adjunto.
Este comportamiento reproducible no demuestra que haya causado el reporte de
la asesora; para atribuirlo se necesita un caso concreto y su auditoría.

Se añade `CHATWOOT_WEBHOOK_REJECTED` con motivo de rechazo y IDs numéricos mínimos,
sin cuerpo, teléfonos, tokens ni encabezados. Los eventos aceptados de estado o
actualización registran `CHATWOOT_CONVERSATION_OBSERVED` con estado y assignee si
el proveedor los incluye. Se conserva el rechazo por scope: no se relajan firmas
ni aislamiento para eliminar los 403. Un evento JSON que no sea un objeto recibe
400 en lugar de causar un error interno.

## Comprobación en el panel del negocio

1. En la cuenta/inbox de Memo's, usar temporalmente **Todas** en lugar de **Mías**
   o **Sin asignar** y revisar todos los estados, incluidas resueltas y pospuestas.
   Limpiar filtros de equipo, etiquetas y lectura. Cambios de asignación o estado
   pueden sacar un caso de una vista sin borrarlo.
2. Buscar el **mismo ID** de conversación y revisar su actividad: cambios de asesor,
   resolución manual/automática, posposición y reapertura. Si reaparece con el mismo
   ID, es el mismo ticket; otro ID puede corresponder a una nueva derivación.
3. Revisar reglas de automatización, cierre automático por inactividad y fecha de
   posposición. Un mensaje real nuevo puede reabrir el caso y moverlo al principio
   de una lista ordenada por última actividad. Registrar quién o qué produjo el
   cambio antes de modificar esas reglas.
4. Si se reproduce, anotar ID, hora de Bogotá, vista/filtros y estado/asignación
   antes y después. Comparar con `CHATWOOT_CONVERSATION_OBSERVED`. No asumir que
   eventos retrasados reflejan el estado remoto actual; comparar timestamps y la
   actividad del ticket, en especial si se resuelve y reabre el mismo caso.
5. Para los 403, comprobar que los callbacks del inbox de Memo's apuntan a su
   Railway y que los IDs corresponden a su cuenta/inbox. Un account webhook puede
   incluir eventos ajenos al inbox o sin inbox; esos rechazos pueden ser esperados.
   No copiar secretos de otra marca ni eliminar los controles de aislamiento.
   Los nuevos logs permiten distinguir esta situación sin exponer el payload.

No se inspeccionó la configuración interna del Chatwoot de producción: el entorno
compartido no tiene credenciales de ese negocio. No se alteró ningún servicio,
conversación ni configuración real durante esta revisión.

Las pruebas usan un proveedor simulado con estados `resolved`/`snoozed`: una nota
privada no lo reabre; un mensaje real sí. También verifican texto/adjuntos, bloqueo
de eventos de otro scope y ausencia de datos privados en el diagnóstico.
