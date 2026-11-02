# Salud de Tanaka: Gemini, PDF y adjuntos — 28 de octubre de 2026

## Conclusión y alcance

El extracto `logs.1793198181279.log.txt` contiene 1.000 líneas, aproximadamente
08:22–09:36, hora Colombia. El SHA observado es
`7f03ba5bcb80f55fdfd64d018d745fdaba1f0c90`: contiene el PR #139 de Gemini y el
PR #138 de adjuntos. No es evidencia de todo el día ni de Memo's/Velvet.
Los timestamps externos de Railway están intercalados; las horas de los eventos
operativos y de HTTP tienen prioridad para correlacionar el flujo.

| Incidente | Evidencia | Causa respaldada |
| --- | --- | --- |
| Gemini a las 09:20 | Google responde 503 a las 09:20:07.690, 09:20:09.105 y 09:20:12.016; `ServerError` indica alta demanda. Líneas 186–250. | Tres intentos de una sola solicitud; persiste la indisponibilidad del proveedor. El arreglo anterior de clasificación/reintento está activo. |
| Otro caso derivado | Conversación 681, 08:22:48, razón de inferencia; su error original quedó fuera del comienzo del archivo. | No se puede atribuir ese caso a una clase de error concreta. La segunda derivación confirmada es 684, 09:20:14. |
| PDF del asesor | Mensaje 4716 / conversación 674: Chatwoot 302, Storage 200, `attachment_too_large` antes de subir a Meta. Líneas 398–411. | Rechazo local por tamaño. El log antiguo no contiene tamaño exacto ni límite efectivo; el usuario y la captura identifican PDF. |
| Varias fotos | Mensaje 4718 / conversación 674: nueve subidas y nueve solicitudes de envío aceptadas por Meta; línea 560 registra `attachment_count=9`, 31,266 s. | El backend procesa lotes. Esta aceptación no equivale a entrega al teléfono. La selección de una sola foto desde el celular no se reproduce con este log. |

El límite genérico tiene default **25 MiB** y se introdujo en
`e805253182a24b0f4f6e79160e3c4e8110da125c`, el 20 de agosto. No lo introdujo el
arreglo de Gemini de ayer ni el cambio de fotos del 26 de octubre. La nota
privada detallando el fallo sí es una mejora reciente de visibilidad.
No consta si el deployment tiene una variable que sustituye ese default.

No aparecen errores Gemini de cuota/créditos, autenticación, timeout de petición
ni vencimiento RQ en este extracto. El caso de 503 completo termina en unos
14,75 segundos de job, lejos del timeout de 180 segundos visto ayer. No hay
evidencia aquí para subir timeouts, revertir el arreglo o atribuir el PDF a S3.

También hay 295 llamadas Supabase correctas, 27 POST Meta con HTTP 200 y
respuestas Chatwoot correctas. Los 19 rechazos de scope preservan el aislamiento
entre inboxes; no son fallos del PDF de Tanaka. Los 27 marcadores BOOT tienen el
mismo SHA y acompañan imports de jobs RQ; no representan 27 redeployments.

## Deployment aclarado por el operador

Los commits `c234a05` y `7f03ba5`, a las 07:37 y 07:42 de Colombia, modificaron
únicamente `src/clients/memos/system_instruction.txt` con el mensaje
`Update SI via Dashboard`. El dashboard escribe un commit en la rama configurada.
El usuario confirmó que actualizó las instrucciones de Memo's desde Lovable y
que ese fue el origen del deployment. Este punto queda aclarado. No se modifica
la integración GitHub/Railway, las instrucciones nuevas ni sus precios.

## Cambios preparados

1. **PDF asesor → WhatsApp:** límite independiente
   `CHATWOOT_MAX_DOCUMENT_BYTES`, default y máximo 100 MiB (104857600 bytes).
   Puede reducirse por deployment. El límite genérico conserva su aplicación a
   otras clases de adjunto y al sentido cliente → Chatwoot. No se elevan todos
   los límites indiscriminadamente.
2. **Memoria y recursos:** PDF descargado en bloques a un archivo temporal y
   subido mediante multipart transmitido por bloques con
   `requests-toolbelt==1.0.0`. Se comprueba tamaño declarado y tamaño real leído,
   incluso si falta o es incorrecto `Content-Length`. Se verifica la cabecera
   PDF. El archivo temporal se cierra al salir, también con fallos de red o Meta.
   Los bytes del documento no se recomprimen ni se convierten en imágenes.
3. **Diagnóstico del archivo:** tipo, límite, tamaño declarado y bytes descargados
   en logs sin URLs firmadas ni contenido. La nota al asesor incluye el límite
   cuando el archivo es demasiado grande.
4. **Diagnóstico de Gemini:** evento `GEMINI_OPERATION_FAILED` con categoría,
   estado HTTP, modelo, duración, SHA y correlación heredada de mensaje/job.
   La derivación distingue indisponibilidad de Google, cuota, autenticación,
   timeout y respuesta JSON inválida. No imprime el cuerpo del proveedor ni el
   prompt. Conserva el caso específico de créditos agotados.
5. **Pruebas de lotes:** nueve adjuntos se procesan en orden. Un fallo tras
   aceptar una parte no marca todo como enviado ni reenvía la parte aceptada.

Se conservan modelo, prompt, tres intentos de Gemini y presupuestos de tiempo.
Este cambio no elimina una indisponibilidad sostenida de Google. Tampoco
habilita un modelo alternativo sin comprobar su disponibilidad y comportamiento
comercial. Las conversaciones ya derivadas siguen bajo control humano.

Es código genérico para Tanaka, Memo's, Velvet y cualquier nuevo BUSINESS_ID,
con credenciales propias. No hay cambios SQL: tanto bootstrap nuevo como upgrade
existente mantienen el contrato. No se modifican colas, secretos, bucket,
Chatwoot/Sidekiq, catálogos ni reglas comerciales.

## Límites de la comprobación

Las consultas HTTP de solo lectura a Railway Status, AI Studio Status,
Google Cloud Status, `/health` del bot y `/api` de Chatwoot devolvieron
`ProxyError` desde este entorno. No se obtuvo un estado público actual y no se
puede afirmar que exista o haya terminado una incidencia general. Ese bloqueo
de la herramienta no demuestra una caída de producción. El diagnóstico anterior
se basa en las respuestas reales de los proveedores presentes en el log.

El presupuesto de descarga PDF se comprueba entre bloques a los 60 segundos.
Los timeouts de requests/HTTPX limitan fases e inactividad; no son garantías
absolutas de tiempo extremo a extremo. El worker conserva sus límites existentes.
Un PDF necesita espacio temporal suficiente: hasta 100 MiB por transferencia
concurrente. No se añade un volumen persistente. La prueba de firma inicial no
es una validación completa de la estructura interna del PDF.

## Revisión, despliegue y reversión

1. Revisar el cambio contra `42-actualizacion-de-precios`; la base es `7f03ba5`.
   Esta preparación no despliega producción. Mantener las nuevas instrucciones
   comerciales de Memo's incluidas en esa base.
2. Al aprobar el despliegue, instalar requirements y actualizar web/RQ del negocio
   seleccionado al mismo SHA. Revisar qué servicios siguen la rama antes del
   merge: una rama compartida puede disparar sus despliegues automáticos.
   No hace falta redeploy de Chatwoot web o Sidekiq para este arreglo.
3. No vaciar Redis, no repetir jobs fallidos en bloque y no usar Retry all.
   Los jobs pendientes usan el mismo contrato. El código no reproduce por sí
   solo un mensaje fallido ni reabre conversaciones en atención humana.
4. En el chat de prueba confirmado por el operador: subir un PDF pequeño y uno
   válido mayor de 25 MiB y menor de 100 MiB como mensajes nuevos; confirmar
   recepción y apertura en WhatsApp. Comprobar bytes/hash del PDF recibido.
   Repetir lote de fotos y verificar cliente → Chatwoot. Registrar message ID,
   SHA de web/RQ y aceptación de Meta; `sent` no prueba entrega al dispositivo.
5. Para Gemini, observar una nueva solicitud de prueba, con SHA y evento:
   respuesta normal o tres intentos y causa específica en la derivación. Si
   persiste 503, corresponde mantener atención humana y revisar disponibilidad
   del modelo/proyecto en Google; no es una razón para reiniciar bases o colas.
6. Si el portafolio requerido ya está publicado en los catálogos del negocio,
   puede usarse el comando público `/catalogo ID_DEL_CATALOGO` con su ID real
   verificado. Reutiliza el catálogo administrado y evita volver a subirlo a
   Chatwoot. No asumir que cualquier PDF sea ese catálogo.
7. Reversión: restaurar `7f03ba5` en web/RQ del negocio conservando variables y
   datos; vuelve el límite anterior de PDF y el diagnóstico genérico de Gemini.
   No restaurar credenciales revocadas ni una versión anterior de Chatwoot.

La validación automatizada usa proveedores simulados y contenido sintético.
Comando ejecutado: `.venv/bin/python /workspace/.cloud/whatsapp-bot-shared/test.py --tb=short`.
Resultado: **690 pruebas aprobadas, tres omitidas** por ausencia de los binarios
PostgreSQL y tres avisos de deprecación existentes. Se ejercitó la transferencia
de 26 MiB con comparación de SHA-256 y el encoder multipart real en bloques,
límites de tamaño aun con headers ausentes/incorrectos, limpieza tras fallos,
aislamiento por marca, nueve fotos en secuencia y clasificación de errores del
SDK real mediante proveedores simulados.
La entrega real de PDFs y la recuperación de Google requieren la comprobación
posterior al despliegue; no se declaran verificadas desde esta preparación.
