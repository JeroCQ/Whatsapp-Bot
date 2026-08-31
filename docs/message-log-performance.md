# Evaluación de desempeño comercial del bot

## Alcance y criterio

Análisis reproducible de `message_logs_rows.csv`, con 2.771 eventos de 430 contactos entre el 24 y el 31 de agosto de 2026. Las cifras son indicadores del chat, no un reemplazo del sistema de pedidos o pagos. Se considera **conversión fuerte** únicamente cuando el log registra comprobante de pago recibido/enviado o un pedido cerrado; no se cuenta como venta una consulta, una cotización ni el envío del catálogo.

## Métricas ejecutivas

| Indicador | Resultado |
| --- | ---: |
| Contactos únicos | 430 |
| Contactos que enviaron 2+ mensajes | 210 (48,8%) |
| Contactos que recibieron catálogo | 309 (71,9%) |
| Checkouts iniciados (solicitud de datos) | 10 (2,3%) |
| Conversiones fuertes observables | 3 (0,7% de contactos) |
| GMV mínimo observable | COP $126.500 |
| Ticket promedio observable | COP $42.167 |
| Transferencias a humano | 162 eventos / 159 contactos |
| Transferencias causadas por falta de créditos | 118 (72,8% de los handoffs) |
| Mensajes de fallback por retraso | 225, afectando 127 contactos (29,5%) |
| Respuesta del bot | mediana 4,2 s; p90 10,6 s |
| Handoff hasta primera respuesta humana | mediana 21,7 min; p90 67,9 min |
| Handoffs atendidos antes de 1 hora | 86,2% |

El embudo observable es 430 contactos → 210 conversaciones con interacción → 10 inicios de checkout → 3 conversiones fuertes. La caída más importante está antes del checkout: solo 4,8% de los contactos que interactúan llegan a que se soliciten sus datos. El GMV es un **piso**, porque el archivo no tiene un campo estructurado de estado, identificador de pedido o valor cobrado y podría haber ventas cerradas fuera del chat.

## Lectura comercial

- **Demanda alta, cierre bajo:** 430 contactos en poco más de siete días, pero una conversión trazable de solo 0,7%. La oportunidad principal es convertir consultas de precio, ubicación y envío en una recomendación y un cierre concreto.
- **El catálogo no basta:** llega a 71,9% de los contactos, mientras solo 48,8% responde una segunda vez. Conviene acompañarlo con 2–3 opciones destacadas, precio, unidades y una pregunta simple de elección.
- **Fricción en checkout:** pedir cédula, correo y mes de cumpleaños antes de cerrar produjo objeciones visibles. Solicitar primero solo producto, nombre, teléfono, dirección/barrio y pago; dejar datos opcionales o de facturación para después.
- **El flete bloquea ventas:** las cotizaciones fuera de Cali requieren handoff y algunas conversaciones terminan al conocer el envío. Mostrar rangos por ciudad/zona temprano y crear combos con umbral de envío subsidiado.

## Mejoras técnicas prioritarias

1. **Eliminar la dependencia operativa de créditos:** alertas de saldo, recarga automática, proveedor/modelo de respaldo y circuit breaker. La falta de créditos explica 72,8% de las transferencias y deteriora tanto conversión como costo humano.
2. **Instrumentar el embudo:** guardar `lead_id`, `conversation_id`, `product_viewed`, `checkout_started`, `order_id`, `payment_status`, `order_value`, `handoff_reason` y timestamps. Sin estos campos no se puede reportar revenue ni conversión real con certeza.
3. **Cotizador determinista:** integrar tarifas por ciudad, barrio, peso y cadena de frío para resolver envíos sin asesor. Si no existe tarifa exacta, entregar rango y plazo antes del handoff.
4. **Checkout corto y con estado:** recopilar datos por etapas, validar únicamente los obligatorios y recordar lo ya suministrado. No volver a pedir correo, cédula o cumpleaños si no son indispensables.
5. **SLA y cola priorizada:** marcar como alta prioridad comprobantes, pedidos con productos elegidos y abandono en checkout. Objetivos sugeridos: p90 humano <15 min para intención de compra y <5 min para comprobantes.
6. **Pruebas y observabilidad:** monitorear tasa de fallback, disponibilidad del modelo, latencia, handoffs por causa, abandono por paso y discrepancias entre precio/flete informado y cobrado.

## Mejoras humanas prioritarias

1. **Responder con cierre guiado:** en lugar de “¿cómo te ayudo?”, resumir producto + precio + entrega y ofrecer dos opciones: “¿1 paquete o el combo de 2?”.
2. **Reducir el formulario inicial:** explicar por qué se requiere cada dato y hacer opcionales correo, cumpleaños y documento cuando la normativa/proceso lo permita.
3. **Protocolo de recuperación:** al retomar tras una falla, leer el contexto y continuar desde la última intención; evitar el mensaje genérico “ya estamos de vuelta”.
4. **Manejo de objeción de flete:** ofrecer recogida, punto aliado, pedido agrupado, mínimo para subsidio o fecha de ruta; no limitarse a repetir que la transportadora calcula el valor.
5. **Disciplina de CRM:** al verificar un pago, registrar venta, valor, productos, responsable y despacho. Cerrar cada conversación con estado y motivo para medir desempeño individual y del bot.

## Metas iniciales (30 días)

- Disponibilidad del bot >99,5% y handoffs por fallo técnico <2%.
- Conversión fuerte trazable ≥3% de contactos (al menos 13 ventas por cada 430 leads comparables).
- Inicio de checkout ≥8% y abandono de checkout <40%.
- p90 de primera respuesta humana <15 minutos en leads de compra.
- 100% de ventas con `order_id`, valor y estado de pago estructurados.

Para regenerar las cifras sin exponer teléfonos ni contenidos, ejecutar `python scripts/analyze_message_logs.py`.
