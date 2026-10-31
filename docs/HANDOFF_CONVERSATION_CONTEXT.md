# Último mensaje del bot durante un handoff

El ticket de Chatwoot se crea antes de enviar la respuesta final del modelo a WhatsApp.
Así el bot puede comprobar que existe el caso antes de afirmar al cliente que ha avisado
al equipo. El resumen inicial contiene el historial disponible en ese momento, por lo que
todavía no incluye esa última respuesta.

Después de que Meta acepte la respuesta final, el backend la registra y añade a la misma
conversación una **nota privada de continuidad del resumen**:

```text
Continuación del resumen de handoff
Último mensaje del bot al cliente:
[texto completo enviado a WhatsApp]
```

El texto conserva sus saltos de línea y no se vuelve a resumir con Gemini. La nota aclara
que la aceptación de Meta no confirma la recepción en el dispositivo del cliente. Su
webhook es una nota interna: no se reenvía como otro mensaje a WhatsApp.

Si después falla un catálogo y el flujo existente envía un aviso adicional al cliente,
ese aviso también se añade al contexto privado del handoff ya creado, manteniendo el orden.

Si Meta rechaza el envío o su resultado es incierto, esa respuesta no se presenta como
enviada en la nota de continuidad. Si falla publicar la nota en Chatwoot, se registra
`HANDOFF_REPLY_CONTEXT` con resultado `not_confirmed` o `context_error`, sin volver a
enviar el mensaje al cliente ni generar un nuevo aviso técnico para él. El evento no
incluye el contenido de la conversación. No hay un reintento automático de esa nota.

Si el primer intento de crear el handoff queda pendiente, el mensaje realmente enviado
se conserva en el historial. Cuando se crea o recupera el ticket posteriormente, su
resumen inicial puede incluir ese mensaje a través del historial existente; no se
copia como enviado el borrador que se haya sustituido por un aviso de atención pendiente.

Aplica igual a Tanaka, Memo's, Velvet y cualquier `BUSINESS_ID`. No añade datos cruzados,
SQL, columnas, variables ni claves. Es compatible con proyectos nuevos y existentes
que ya usan los esquemas canónicos. No modifica conversaciones históricas.

Después del merge y despliegue del backend y su worker, usa un chat de pruebas del negocio:
provoca una transferencia, compara la respuesta de WhatsApp con la nota privada de
continuidad y verifica que el cliente la recibió una sola vez. No requiere redeploy de
Chatwoot, Sidekiq ni sus dependencias.
