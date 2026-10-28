# Plan futuro: Gemini Flash con respaldo de OpenAI

Estado: **documentación de una propuesta; no implementada ni activada**.

El merge de este documento mantiene el funcionamiento actual con
`gemini-flash-latest`. No instala clientes nuevos, no llama a OpenAI y no exige
API key, variables, SQL ni acciones manuales. Las variables descritas abajo son
una propuesta para una implementación posterior, no instrucciones para hoy.

## Objetivo y decisión inicial

Conservar Gemini Flash como principal por su tono y rapidez, y evaluar
`gpt-4.1-mini` mediante la API de OpenAI como un único respaldo independiente.
Confirmar disponibilidad del modelo, calidad, precio y latencia antes de
habilitarlo. No se propone operar una GPU propia ni añadir un segundo respaldo
en la primera versión.

El respaldo mejoraría continuidad frente a problemas transitorios de Gemini.
No garantiza disponibilidad absoluta ni reduce sustancialmente el costo normal
si casi todas las conversaciones siguen usando Flash.

## Cómo conservar la conversación

Los modelos recibirán las instrucciones del negocio, el catálogo vigente, el
historial relevante y la memoria del pedido guardados por el backend. No se
crearán chats nuevos de WhatsApp ni se dependerá de una sesión alojada en un
proveedor para recordar el pedido.

La selección del respaldo será persistente por negocio y conversación. Tras
obtener una respuesta válida del respaldo, ese chat continuará con él hasta un
cierre explícito, resolución del asesor o reset. No alternará de modelo en cada
mensaje ni se reiniciará la selección por un saludo o un «gracias» mientras
exista un pedido pendiente. El historial y la memoria se conservarán al volver
al principal.

Las conversaciones nuevas usarán Flash cuando su disponibilidad se haya
restablecido. Una implementación deberá definir y probar la detección de esa
recuperación y, si se añade un circuito de indisponibilidad, sus tiempos y
límites. No se hará una petición de prueba por cada cliente.

## Flujo propuesto

1. Leer el estado vigente y respetar la atención humana antes de inferir.
2. Usar la ruta persistida o Flash como principal.
3. Ante un fallo transitorio elegible de Flash, intentar el respaldo dentro de
   un presupuesto total de tiempo y costo. Evitar encadenar tres esperas largas
   por cada proveedor. Distinguir 503, timeout, cuota, credenciales, errores del
   código y respuestas con formato inválido; no tratarlos todos como una caída.
4. Validar la respuesta con el mismo contrato estructurado y las reglas del
   backend: precios, catálogos, presentaciones, horarios y precedencia del handoff.
5. Persistir la ruta del respaldo antes de entregar su respuesta. Revalidar la
   ventana original de WhatsApp y el control humano antes de enviar.
6. Enviar una sola respuesta al cliente. Si ambos proveedores fallan, derivar al
   asesor: causa técnica en notas privadas y transición natural al cliente.
   Confirmar la creación del caso antes de afirmar una transferencia completada.

Los cambios de modelo deben ocurrir antes del envío a Meta. No se usarán como
motivo para repetir un mensaje con resultado de entrega ambiguo. La llegada de
una respuesta del cliente o la intervención humana seguirá invalidando los
trabajos antiguos conforme a los mecanismos existentes.

`gpt-4.1-mini` no sustituye directamente la transcripción de una nota de voz.
Antes de cubrir audio, se deberá evaluar una ruta de transcripción compatible,
con su costo y límites de formatos/tamaño. La primera implementación deberá
declarar expresamente si cubre solo texto o también audio; nunca enviar bytes de
audio a un endpoint de chat que no los admite.

## API key: qué es y dónde se obtendría

La API key identifica al backend ante OpenAI y permite facturar sus solicitudes.
Es distinta de la clave de Gemini. **Una suscripción ChatGPT Plus o Pro no
incluye el consumo de la API de OpenAI.** Este se configura y factura en
OpenAI Platform.

Solo cuando se decida implementar y activar el respaldo:

1. Entrar en [OpenAI Platform](https://platform.openai.com/) e iniciar sesión.
2. Revisar **Billing** y configurar un medio de pago o saldo según las opciones
   disponibles. Consultar precios vigentes; no asumir que la suscripción de
   ChatGPT habilita la API.
3. En **Projects**, crear un proyecto para el negocio, por ejemplo
   `WhatsApp Tanaka`. Repetir el aislamiento para Memo's y Velvet.
4. Dentro del proyecto, abrir **API keys** y crear una clave de servidor con los
   permisos mínimos necesarios. Para producción, evaluar una cuenta de servicio
   propia del proyecto. Los nombres exactos de las pantallas pueden cambiar.
5. Guardar la clave cuando se muestre. No pegarla en este chat, Lovable/frontend,
   GitHub, documentos, capturas ni logs. No se necesitan sus valores para
   preparar el código.
6. Revisar límites de uso, acceso al modelo, consumo y alertas de presupuesto.
   Una alerta de presupuesto no debe confundirse con un corte garantizado de
   gasto: comprobar qué límites son efectivos y añadir controles en la aplicación.
7. Después de que el código de respaldo esté implementado y probado, guardar
   la clave como variable privada del servicio web y del worker RQ **del mismo
   negocio** en Railway. No configurar nada en Chatwoot/Sidekiq para este fin.

Se recomienda un proyecto y una clave separados por negocio, para controlar
consumo y permisos sin compartir datos ni secretos entre marcas. Verificar los
permisos efectivos de cada clave y las condiciones de tratamiento de datos del
proveedor antes de enviar conversaciones al nuevo servicio.

## Configuración propuesta para una versión futura

Estos nombres **todavía no habilitan ninguna funcionalidad**:

| Variable propuesta | Valor inicial previsto | Comportamiento a implementar |
| --- | --- | --- |
| `AI_FALLBACK_ENABLED` | `false` por defecto | Ausente o desactivada: comportamiento actual, sin llamadas ni requisitos de OpenAI. |
| `OPENAI_API_KEY` | Secreto del proyecto del negocio | Solo requerida al activar el respaldo; nunca incluida en respuestas o logs. |
| `OPENAI_MODEL` | `gpt-4.1-mini` | Modelo evaluado y disponible en la cuenta; selección independiente del dashboard de Gemini. |

Los presupuestos de tiempo, máximo de intentos y controles de gasto se definirán
en la implementación. No se propone cambiar ahora los timeouts, las claves ni
las variables existentes de Gemini.

La ausencia de una clave deberá conservar el arranque normal mientras el
respaldo esté desactivado. Activación explícita con configuración incompleta
deberá detectarse antes de dar el servicio por saludable, sin exponer secretos.
No se hará una llamada externa a OpenAI durante un despliegue solo documental.

## Trabajo técnico que quedaría pendiente

- Adaptador de OpenAI con timeout y número de intentos explícitos, validación de
  JSON y errores seguros; sin reintentos ocultos ni cuerpos de error en logs.
- Selección persistente por conversación que sobreviva a reinicios y múltiples
  workers, con transición segura al resolver/resetear la conversación.
- Protección ante resultados antiguos, control humano y ventanas de mensajería.
- Métricas por proveedor, modelo, mensaje, intento y SHA, con tokens y tiempos
  cuando estén disponibles, sin registrar contenido adicional del cliente.
- Si se necesitan columnas o funciones SQL, modificar conjuntamente
  `supabase/bootstrap.sql` y `supabase/upgrade_existing_brand.sql`, con pruebas
  de paridad. No se reutilizarán credenciales, datos ni colas de otra marca.
- Compatibilidad con instalaciones nuevas y existentes y con despliegues que
  sigan usando solamente Gemini. No reescribir memoria comercial para guardar
  información interna de selección de proveedor.

## Pruebas antes de activarlo

Primero usar proveedores simulados y datos sintéticos. Verificar que Flash
exitoso no llama a OpenAI; que un fallo elegible produce una sola respuesta;
que el respaldo continúa la misma conversación y conserva datos/precios; y que
un fallo de ambos deriva al asesor sin mostrar detalles técnicos al cliente.

Incluir persistencia entre workers/reinicios, cierre/reset, intervención humana,
respuesta nueva del cliente, ventana vencida, falta de credenciales, respuesta
inválida y aislamiento entre Tanaka, Memo's, Velvet y un negocio futuro.

Después comparar unas 30 conversaciones comerciales representativas: tono,
precios exactos, pedidos, domicilios, rechazos, mayoristas y derivaciones. Medir
calidad y latencia de respuesta completa, incluido el percentil 95. Un catálogo
de capacidades o precios no demuestra equivalencia de calidad con Flash.

## Activación futura y reversión

1. Aprobar un cambio de implementación independiente y confirmar el alcance de
   texto/audio, los precios y el tratamiento de datos.
2. Aplicar migraciones necesarias en el Supabase aislado, si las hubiera;
   desplegar web y RQ al mismo SHA con el respaldo desactivado.
3. Configurar y probar la clave únicamente en el negocio piloto y su chat de
   pruebas confirmado. Verificar ambas rutas sin mensajes a clientes reales.
4. Activar el piloto, revisar consumo, errores, latencia y calidad; ampliar por
   negocio solo cuando los resultados lo justifiquen.
5. Para desactivar, el código futuro deberá permitir volver al funcionamiento
   solo Gemini sin eliminar historial, vaciar Redis ni reiniciar bases de datos.
   Debe definir qué ocurre con las conversaciones persistidas en el respaldo.

**Después del merge de este documento no hay pasos de activación que ejecutar.**

Referencias para consultar el día de la activación:
[API keys](https://platform.openai.com/api-keys),
[precios API](https://openai.com/api/pricing/),
[API y facturación de ChatGPT](https://help.openai.com/en/articles/8156019).
