# Evaluación de desempeño comercial del bot

## Alcance y criterio

El archivo completo tiene 2.771 eventos de 430 contactos entre el 24 y el 31 de agosto de 2026. Para evaluar la efectividad comercial del bot se excluyen por completo los 118 contactos cuya conversación tuvo en algún momento un error de créditos. La cohorte evaluada queda en 312 contactos y 1.859 eventos. Se considera **conversión fuerte** únicamente cuando el log registra comprobante de pago recibido/enviado o un pedido cerrado.

## Métricas ejecutivas

| Indicador | Resultado |
| --- | ---: |
| Contactos evaluados | 312 (118 excluidos por créditos) |
| Contactos que enviaron 2+ mensajes | 142 (45,5%) |
| Contactos que recibieron catálogo | 298 (95,5%) |
| Contactos sin catálogo | 14 (4,5%) |
| Formularios de checkout ofrecidos | 8 (2,6%) |
| Pedidos con cotización total | 5 (1,6%) |
| Conversiones fuertes observables | 3 (1,0% de contactos) |
| GMV mínimo observable | COP $126.500 |
| Ticket promedio observable | COP $42.167 |
| Handoffs en la cohorte evaluada | 41 eventos / 41 contactos |
| Conversaciones resueltas en la cohorte | 6; 0 ventas demostrables |
| Contactos retomados después de `RESOLVED` | 0 dentro de la cohorte limpia |
| Respuesta del bot | mediana 4,7 s; p90 13,8 s |
| Handoff hasta respuesta humana | mediana 42,5 min; p90 795,6 min |
| Handoffs atendidos antes de 1 hora | 62,5% |

El embudo limpio es 312 contactos → 142 conversaciones con interacción → 5 pedidos con total cotizado → 3 conversiones fuertes. El GMV es un **piso**, porque el archivo no tiene un campo estructurado de estado, identificador de pedido o valor cobrado.

## Por qué faltó el catálogo

- Tras excluir toda conversación tocada por créditos quedan **14 faltantes reales**: siete muestran fallbacks sin causa estructurada, seis eran saludos o consultas solo de ubicación y uno fue incumplimiento del modelo ante una consulta explícita de producto.
- Había además un error de medición: el sistema escribía `Archivos enviados` cuando Gemini los solicitaba, aunque `send_presaved_file` fallara. Por tanto, 309 es el máximo registrado, no una garantía histórica de entrega efectiva.

La corrección convierte el catálogo en una regla determinista del primer contacto: la aplicación lo agrega aunque el modelo no lo solicite, registra éxito solo si Meta confirma el envío y vuelve a intentarlo en el siguiente turno si falla. La comprobación usa un marcador durable, no solo los últimos mensajes.

## Resultado después de `RESOLVED`

En la cohorte limpia hay seis cierres y ningún contacto volvió a escribir después. Ninguno contiene evidencia de venta cerrada: son consultas informativas, cotizaciones de envío o formularios abandonados. En el archivo completo sí hay 10 contactos retomados después de `RESOLVED`, pero todos quedan excluidos porque su historial también fue afectado por créditos; ninguno terminó en una venta demostrable. `RESOLVED` significa “ticket cerrado”, no “venta cerrada”.

## Auditoría de checkout

El indicador anterior de “10 checkouts” mezclaba formularios genéricos con pedidos reales, y una de las tres conversiones no contenía ese formulario literal. No era correcto restar 10 − 3 y concluir que había siete abandonos. En la cohorte limpia hubo ocho formularios ofrecidos: dos terminaron en conversión fuerte y seis no. Solo cinco conversaciones alcanzaron una cotización total; tres cerraron y dos abandonaron.

Los seis teléfonos con formulario pero sin conversión fuerte se pueden mostrar para auditoría manual ejecutando `python scripts/analyze_message_logs.py --show-phones`. Los dos abandonos que sí llegaron a total cotizado son `573117766949` y `573148703887`.

## Acción requerida en Supabase

En el proyecto existente se debe ejecutar una vez `supabase/upgrade_existing_tanaka.sql`, o desplegar la migración `20260831000000_conversation_memory.sql`. Esto crea de forma idempotente `customer_data` y `order_summary`. Sin esa actualización el bot sigue operando, pero no puede persistir la memoria estructurada entre ventanas de contexto ni incluirla de forma durable en handoffs.

## Lectura comercial

- **Demanda alta, cierre bajo:** en la cohorte evaluable la conversión trazable es 1,0%. La oportunidad principal es convertir consultas de precio, ubicación y envío en una recomendación y un cierre concreto.
- **El catálogo no basta:** llega a 95,5% de la cohorte, mientras solo 45,5% responde una segunda vez. Conviene acompañarlo con 2–3 opciones destacadas, precio, unidades y una pregunta simple de elección.
- **Fricción en checkout:** pedir cédula, correo y mes de cumpleaños antes de cerrar produjo objeciones visibles. Solicitar primero solo producto, nombre, teléfono, dirección/barrio y pago; dejar datos opcionales o de facturación para después.
- **El flete bloquea ventas:** las cotizaciones fuera de Cali requieren handoff y algunas conversaciones terminan al conocer el envío. Mostrar rangos por ciudad/zona temprano y crear combos con umbral de envío subsidiado.

## Mejoras técnicas prioritarias

1. **Eliminar la dependencia operativa de créditos:** alertas de saldo, recarga automática, proveedor/modelo de respaldo y circuit breaker.
2. **Instrumentar el embudo:** guardar `lead_id`, `conversation_id`, `product_viewed`, `checkout_started`, `order_id`, `payment_status`, `order_value`, `resolution_outcome`, `handoff_reason` y timestamps. Sin estos campos no se puede reportar revenue ni conversión real con certeza.
3. **Cotizador determinista:** integrar tarifas por ciudad, barrio, peso y cadena de frío para resolver envíos sin asesor. Si no existe tarifa exacta, entregar rango y plazo antes del handoff.
4. **Checkout corto y con estado:** recopilar datos por etapas, validar únicamente los obligatorios y recordar lo ya suministrado. La memoria estructurada conserva datos y pedido aunque salgan de la ventana reciente, y el handoff los muestra al asesor.
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
- Conversión fuerte trazable ≥3% de contactos evaluables (al menos 9 ventas por cada 312 leads comparables).
- Inicio de checkout ≥8% y abandono de checkout <40%.
- p90 de primera respuesta humana <15 minutos en leads de compra.
- 100% de ventas con `order_id`, valor y estado de pago estructurados.

Para regenerar las cifras sin exponer teléfonos ni contenidos, ejecutar `python scripts/analyze_message_logs.py`.
