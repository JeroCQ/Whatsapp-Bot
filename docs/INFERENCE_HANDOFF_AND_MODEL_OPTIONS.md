# Derivación natural y opciones de modelo — 28 de octubre de 2026

## Cambio preparado

Después de agotar los intentos de inferencia, el bot conserva el motivo técnico
en el estado, el evento operativo y las notas privadas de Chatwoot. El cliente
recibe únicamente:

> Te comunico con nuestro equipo para continuar ayudándote.

La creación del caso precede a ese mensaje. Si el caso sigue pendiente o su
creación no está confirmada, el texto es neutral y no afirma una transferencia
completada:

> Gracias por tu mensaje. Permíteme un momento para continuar ayudándote.

Aplica también a errores de transcripción y al aviso de recuperación de errores
de procesamiento. Las respuestas comerciales correctas mantienen su texto. Las
causas de cuota, créditos, HTTP 503, timeout o formato JSON no se envían al
cliente. El asesor conserva el contexto y el motivo específico; si Chatwoot no
está disponible, el mecanismo existente conserva la solicitud pendiente.
No se vuelven a enviar avisos por un resultado ambiguo de un POST a Meta.

Validación: **718 pruebas aprobadas, tres omitidas** por ausencia de binarios
PostgreSQL; tres avisos de deprecación existentes. Se probaron texto/audio,
creación confirmada/pendiente/ambigua, notas privadas, orden de creación antes
del aviso al cliente y ausencia de seguimientos durante atención humana para
Tanaka, Memo's, Velvet y un negocio futuro. Los contadores se comprobaron con
respuestas sintéticas del SDK, sin inferencia real ni contenido de clientes.

El cambio es genérico para Tanaka, Memo's, Velvet y negocios futuros. No modifica
SQL, credenciales, proveedor, modelo, tarifas, prompt comercial ni colas.
Instalaciones nuevas y existentes usan el mismo contrato. El código debe
desplegarse en web/RQ del negocio al mismo SHA; no requiere redeploy de Chatwoot.
No reproduce mensajes fallidos ni reabre automáticamente casos en atención humana.
Base de revisión: `36166c1` (PR #140 integrado). La reversión de esta mejora
restaura ese SHA en web/RQ conservando configuración y datos; restablece los
avisos técnicos anteriores y elimina los nuevos contadores. La verificación
posterior debe confirmar el SHA y un handoff sintético en el chat de pruebas
aprobado por el operador, con causa privada y aviso natural al cliente.

## Medir el costo del modelo real

`gemini-flash-latest` es un alias; su nombre no identifica una versión fija ni
una tarifa histórica. El nuevo evento `GEMINI_USAGE` registra la versión resuelta
que devuelve Google y sus contadores de entrada, respuesta, razonamiento, caché
y total. No contiene prompt, texto de respuesta, audio ni datos de facturación.
Se conserva la correlación de negocio, SHA, job y mensaje de los eventos actuales.

Los contadores se registran solo si el SDK los devuelve como enteros no
negativos. Los campos ausentes se omiten, sin impedir la respuesta. Son una
herramienta de diagnóstico, no una conciliación de facturas: no atribuyen un
costo estimado a intentos fallidos sin metadatos. La caché puede ser una parte de
la entrada total y no debe sumarse de nuevo. El razonamiento puede generar
tokens cobrados además de la respuesta visible, según la tarifa del modelo.

## Comparación orientativa de APIs

No se logró abrir las páginas oficiales de precios de Google, OpenAI y Groq por
`ProxyError` de este entorno. Los valores siguientes proceden del
[catálogo público de LiteLLM](https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json),
consultado el 28 de octubre de 2026. Es una fuente secundaria: sirven para
comparar órdenes de magnitud; confirmar precio y disponibilidad con el proveedor
antes de contratar. No prueban que la cuenta de cada negocio tenga acceso.

USD por un millón de tokens de entrada/salida de texto, sin caché, impuestos,
audio ni modalidades especiales:

| Modelo/API | Entrada | Salida | Evaluación propuesta |
| --- | ---: | ---: | --- |
| Gemini Flash Latest, referencia del catálogo | $0,75 | $3,75 | Conservar inicialmente por el tono y la rapidez que ya prefiere el usuario. No equivale a confirmar la tarifa real de sus llamadas anteriores. |
| Gemini 2.5 Flash-Lite | $0,10 | $0,40 | Candidato económico para probar como principal. Sigue dependiendo de Google; disponibilidad y compatibilidad deben comprobarse. |
| GPT-4.1 mini, API OpenAI | $0,40 | $1,60 | Candidato a primer respaldo de otro proveedor. Validar español, estilo comercial, JSON y latencia real. |
| gpt-oss-120b, alojado en Groq | $0,15 | $0,60 | Candidato de pesos abiertos sin operar GPU propia. La ruta comparada es de texto; no sustituye directamente la transcripción de audio. |
| gpt-oss-20b, alojado en Groq | $0,075 | $0,30 | Candidato aún más económico; requiere especial atención a reglas y situaciones comerciales complejas. |

Ejemplo calculado, **no consumo real del negocio**: 10.000 respuestas con 5.000
tokens de entrada y 300 de salida cada una, sin razonamiento adicional ni caché:

| Referencia | Costo de inferencia del ejemplo |
| --- | ---: |
| Flash Latest | $48,75 |
| Flash-Lite | $6,20 |
| GPT-4.1 mini | $24,80 |
| gpt-oss-120b/Groq | $9,30 |
| gpt-oss-20b/Groq | $4,65 |

Excluye Meta, Railway, Supabase y transcripción. Reintentos, longitud de historial,
razonamiento y tasas de caché cambian el resultado. Un respaldo poco usado mejora
continuidad, pero no reduce mucho el gasto normal del principal.

## Recomendación de continuidad

Conservar Flash como principal mientras se compara un único respaldo de otro
proveedor. GPT-4.1 mini y gpt-oss-120b/Groq son candidatos para una evaluación,
no modelos activados por este cambio. Un segundo modelo de Google puede ayudar
si falla un modelo concreto, pero comparte dependencia del proveedor, permisos
y posiblemente cuota. No ofrece el mismo aislamiento que otro proveedor.

Flujo propuesto para una implementación posterior:

1. Principal: intento y recuperación breve ante error transitorio.
2. Un respaldo evaluado, dentro de un presupuesto total de tiempo/costo. Evitar
   agotar tres esperas largas por cada modelo en cascada.
3. Si tampoco produce una respuesta válida, derivación natural al asesor.

El cambio de modelo debe ocurrir antes de enviar la respuesta a Meta y validar
el mismo contrato JSON, precios, catálogo, horarios y decisiones de derivación.
No se envían respuestas de ambos modelos al cliente. La atención humana y una
respuesta nueva del cliente invalidan trabajos anteriores como hasta ahora.
Las credenciales y el contexto se configuran por negocio, sin copiar datos de
otra marca. No se habilita el envío de conversaciones a un proveedor nuevo en
esta entrega.

Ningún proveedor garantiza cero errores. La continuidad depende también de las
cuotas de la cuenta, capacidad contratada, región y transporte. No hay aquí una
medición que pruebe que un modelo alternativo falle menos o responda más rápido
en estos negocios. Antes de habilitarlo, evaluar conversaciones sintéticas de
cada marca, precios exactos, solicitudes mayoristas, pagos, rechazos, audio y
handoffs; medir respuesta completa y percentil 95, no solo tokens por segundo.

## Modelos de pesos abiertos y GPU propia

Un modelo de pesos abiertos alojado como API puede ser viable y muy económico.
La equivalencia de calidad requiere pruebas; no basta con que el chat suene bien.
El contrato estructurado y las reglas comerciales deben cumplirse también.

Autoalojarlo implica GPU, capacidad ociosa, actualizaciones y operación. Como
ejemplo puramente aritmético, una GPU hipotética a $0,50/h cuesta $365 por 730
horas, antes de disco y operación; **no es una cotización de un proveedor**.
Con el volumen observado de los bots, probar primero APIs de pesos abiertos es
una opción más razonable que mantener GPU propia. El punto de equilibrio real
requiere medir consumo, concurrencia y latencia.

Fuentes oficiales a validar para una contratación:
[Google](https://ai.google.dev/gemini-api/docs/pricing),
[OpenAI](https://developers.openai.com/api/docs/pricing),
[Groq](https://groq.com/pricing).
