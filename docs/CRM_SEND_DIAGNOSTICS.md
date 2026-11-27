# Diagnóstico de errores de Gemini y WhatsApp en Chatty

En **Chats → Humano**, el historial muestra el motivo por el que el bot pidió atención humana. Cuando falla un envío de un asesor, abre el aviso de ese envío y pulsa **Consultar resultado** para consultar el mismo comando.

| Aviso | Qué significa | Qué comprobar |
| --- | --- | --- |
| `Gemini no respondió` con un aviso de facturación y `HTTP 402` | Google rechazó la solicitud de IA por facturación. | Revisa el saldo y el método de pago del proyecto de Google que usa ese bot. En los logs del bot, busca `GEMINI_OPERATION_FAILED`, `reason=provider_billing` y `provider_status=402` en el intervalo del incidente. |
| `Gemini no respondió` con «no hay créditos prepagados» | Google identificó explícitamente el agotamiento de créditos. | Revisa la facturación en Google AI Studio. El diagnóstico existente se reconoce como `prepaid_credits_depleted`. |
| `WhatsApp rechazó el envío porque Chatty no tenía el control de este chat (código 131070)` | Meta rechazó la solicitud de envío del mensaje de atención. | Busca `crm_human_provider_result` y `code=131070` en los logs del bot del negocio. Compara el prefijo del `command_id` y la hora si necesitas escalar a Meta. En Meta Business Suite, revisa el enrutamiento del número en **Configuración → Cuentas de WhatsApp → Conversation routing**. |

Un problema de facturación de Gemini puede hacer que el bot pida atención humana. El envío que escribe el asesor usa WhatsApp Cloud API directamente. Si ambos avisos aparecen en el mismo chat, registra el orden y la hora de cada evento. El código 131070 por sí solo no identifica qué integración tenía el hilo ni qué originó la asignación.

Usa **Consultar resultado** si el estado de una orden es incierto. Esa consulta es de solo lectura para el envío y conserva el `command_id`. Los mensajes con rechazo confirmado y los de resultado incierto tienen estados distintos. Una entrega o lectura confirmada en otro chat no demuestra que se haya recuperado un hilo afectado.

El detalle 131070 queda en el registro del intento del bot para los rechazos que se capturen a partir de la actualización. Los intentos históricos que solo guardaron un rechazo genérico conservan ese diagnóstico en el CRM. Si sus logs contienen el código, usa la hora y el prefijo del comando para correlacionarlos.

[Enrutamiento de WhatsApp](https://developers.facebook.com/documentation/business-messaging/whatsapp/conversation-routing/get-started/) · [Control del hilo](https://developers.facebook.com/documentation/business-messaging/whatsapp/conversation-routing/thread-control/)
