# Datos comerciales de Chatty

En CRM central, `0008_commercial_events` añade eventos comerciales encima de `0007_customer_insights`. `0007` reutiliza la evidencia de promociones que ya existe en central `0006`. No ejecutar un downgrade de la cadena al retirar esta función porque `0007` eliminaría el historial de permisos. Mantener una versión compatible con la revisión instalada y desactivar las banderas si hace falta revertir.

## Activación

1. Publicar las versiones compatibles del API y sync con las banderas desactivadas. Confirmar `/ready` y la conexión con el Supabase central activo.
2. Como propietario existente de `chatty_crm`, correr `python -m crm_service.central_migrate --commercial-metrics` con la conexión ya configurada en el servicio. Confirmar la revisión y que las dos tablas de permisos mantienen sus filas y estados. Esta operación no migra ni mueve tablas de bots.
3. En cada bot activo, habilitar `CRM_CUSTOMER_INSIGHTS_ENABLED=true` y `CRM_PROMOTION_CONSENT_ENABLED=true` en su despliegue existente. En API y sync, fijar `CRM_CUSTOMER_INSIGHTS_BUSINESSES` y `CRM_PROMOTION_CONSENT_BUSINESSES` a la lista de negocios habilitados del registro activo. No usar el modo de publicación piloto ni habilitar otro consumidor.
4. Publicar el panel conectado de Chatty. En Admin, seleccionar un negocio para abrir **Ventas y permisos**. En la conversación autorizada, expandir **Datos del cliente**. El panel muestra estados y no envía mensajes.

## Cobertura

- La medición comercial empieza cuando el trabajador guarda el primer cursor de cada negocio. Las señales de conversación se conservan con el ID del mensaje. Una estimación no se registra como pago. Un comprobante o constancia del cliente queda pendiente hasta una confirmación verificable. Para un pago confirmado sin importe, se cuenta el pedido y el importe queda sin dato.
- La conciliación reconoce confirmaciones y alias explícitos del catálogo. Los productos ajenos al catálogo reconocido y los resultados ambiguos se quedan sin clasificar. Revisar la cobertura en la primera semana y ampliar el vocabulario únicamente con muestras verificadas. Los casos sin respuesta se siguen mostrando por separado y quedan fuera de la conversión entre resultados resueltos.
- Para estimar USD se usa `CRM_COMMERCIAL_MODEL_RATES`, JSON privado del API por versión exacta del modelo. Cada entrada tiene `from` (fecha ISO), `input`, `cached_input` y `output` en USD por millón de tokens según la tarifa comprobada del proveedor vigente. Una versión sin tarifa o un intento sin todos los datos necesarios se muestra sin estimar. Los cargos Meta requieren una fuente de facturación conciliada.
- Las preferencias promocionales se leen por negocio, remitente y contacto. Se mantiene la misma fuente de evidencia que usa el webhook. No activar un disparador de campaña para validar esta función.
