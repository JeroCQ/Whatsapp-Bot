# Permisos para promociones y análisis al devolver a IA

## Operación desde Chatty

En una conversación activa, abre **Datos del cliente → Preparar pregunta** y
envía desde el compositor habitual cuando ya se atendió la consulta. Esto
sirve en un chat nuevo y en el chat abierto por la respuesta del cliente a
una plantilla lícitamente enviada. El asesor debe tener control confirmado
y estar dentro de la ventana de atención. La pregunta usa dos botones e
incluye el texto `BAJA PROMOCIONES`. Las campañas, las plantillas y los
recordatorios de permiso quedan fuera de este flujo.

Meta entrega respuestas de botones al webhook firmado existente. Vinculamos
la respuesta al mensaje exacto y al mismo cliente y negocio. También funciona
una respuesta textual explícita citando la pregunta, así como la baja expresa.
El CRM conserva el mensaje de evidencia y el estado vigente en `chatty_crm`.
El índice por negocio, remitente y estado permite segmentar más adelante.
Un contacto que no responde queda sin respuesta. La aceptación de una
plantilla, su entrega y sus confirmaciones de lectura carecen de efecto en
el estado del permiso.

**Devolver a IA** guarda una tarea cuando el bot confirma el cambio de
control. El worker existente consume la tarea por separado y el bot consulta
la memoria guardada y los mensajes hasta el cierre, incluidos los del
asesor. Reutiliza Gemini para resumir y registra las etiquetas y los IDs
de los mensajes que las sustentan. Los pedidos confirmados se cuentan
desde los registros del cliente en la tabla del negocio si existe una
fuente poblada. En ausencia de registros verificables, Chatty muestra
**Sin conteo verificable**. El resultado permanece en Datos del cliente.
Ningún análisis envía mensajes ni modifica pedidos, etiquetas manuales o
control de la conversación.

## Aplicación en el entorno central existente

1. Despliega el código compatible en las API de CRM y en el worker central
   con ambas listas de negocios vacías. La revisión 0006 sigue siendo válida.
2. Desde la raíz `crm`, ejecuta con la conexión de migración central existente
   `python -m crm_service.central_migrate --customer-insights`. La revisión
   0007 agrega solo las tres tablas privadas e índices. El bot conserva el
   esquema Supabase habitual. Valida que las API estén listas.
3. Despliega el bot desde su rama activa con estos cambios integrados. En
   cada negocio aprobado establece `CRM_PROMOTION_CONSENT_ENABLED=true`
   y/o `CRM_CUSTOMER_INSIGHTS_ENABLED=true`. Usa los secretos de control y
   lectura ya configurados. Ajusta las listas `CRM_PROMOTION_CONSENT_BUSINESSES`
   y `CRM_CUSTOMER_INSIGHTS_BUSINESSES` en la API central y la segunda también
   en el worker. Los negocios se separan con comas.
4. Publica el frontend. En un chat de prueba iniciado por el destinatario,
   comprueba los dos botones y la baja, y confirma una devolución a IA.
   Los negocios fuera de las listas pueden seguir operando con su circuito
   habitual.
