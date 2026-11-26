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

## Respuestas a Black Week

Tanaka y Mochis by Velvet pueden lanzar las plantillas desde su plataforma de
envío habitual a quienes ya autorizaron promociones. Chatty recibe los botones
por el webhook existente. Los reconoce por el número remitente registrado y el
texto exacto del botón en un evento firmado de tipo `button`. Guarda también
los identificadores de la respuesta y del mensaje original. Meta no incluye el
ID de plantilla en este evento, por lo que el registro identifica el mensaje
original y la versión de la pregunta de Black Week.

- **Quiero hacer mi pedido** entra a la cola habitual, con el control del asesor
  y el catálogo del negocio. La oferta se aplica del 27 de noviembre al 5 de
  diciembre de 2026 hasta las 6:00 p. m. de Colombia.
- **Quiero promociones exclusivas** registra autorización para promociones
  futuras del remitente.
- **Prefiero perder las promociones** registra la baja del remitente. La
  plataforma externa de envío debe aplicar esa exclusión a sus pendientes.

Para estos botones, sigue el orden de aplicación anterior con la versión de CRM
que incluye la proyección de Black Week. Una vez aplicada la revisión 0007
existente, actualiza los servicios web y consumidores de Tanaka y Velvet con
el cambio del bot. Comprueba un
callback real por remitente con un número interno autorizado antes de lanzar.
El webhook de la campaña y el motor de pedidos conservan la firma, el destino,
el control humano y la deduplicación existentes.

En el entorno central compartido con una API que requiere la revisión 0006,
usa `python -m crm_service.central_migrate --promotion-permissions` en el
pre-deploy del API del piloto. Usa el rol propietario configurado para instalar
únicamente las dos tablas de permisos. Conserva `alembic_version` y activa
solo el worker del piloto cuando esté listo. El upgrade completo posterior reconoce
las tablas existentes y agrega el análisis cuando ambos servicios son compatibles.
Si el bot ya recibió respuestas antes de habilitar la proyección, usa en el
worker central `python -m crm_service.replay_black_week_permissions tanaka` y
el mismo comando con `velvet` una vez creadas las tablas. Reaplica solo las
respuestas firmadas que ya llegaron a la fuente del CRM, sin repetir envíos.

Los botones verificados guardan un resumen de la plantilla externa en el
historial, con el WAMID del mensaje original. También se pueden registrar los
envíos comprobados desde un CSV con columnas `recipient,sent_at,wamid` mediante
`python import_black_week.py reporte.csv` en el bot del negocio correspondiente.
La columna `wamid` es opcional. Comprueba primero el formato y añade `--apply`
para registrar el reporte. Si la herramienta de envío permite fijar el valor
`biz_opaque_callback_data`, el estado firmado de Meta se puede identificar con
`black-week-2026-v1:tanaka` o `black-week-2026-v1:velvet`. Un reporte agregado
de WhatsApp Manager por sí solo no demuestra un envío a un contacto.
