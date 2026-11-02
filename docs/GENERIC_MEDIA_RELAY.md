# Intercambio genérico de archivos — 30 de octubre de 2026

## Problema y causa raíz

Un sticker WebP de Julián llegó a Memo's a las 11:41:28 de Colombia. Meta
respondió 200 al obtener y descargar el archivo de 125.032 bytes. El bot, ya
pausado para atención humana, lo rechazó con `source_image_decode_failed` antes
de intentar subirlo a Chatwoot. La advertencia privada apareció en la conversación
729. No era una caída de Storage ni un fallo de inferencia de Gemini.

La decodificación local y una lista de firmas de imagen se introdujeron en
`9b3b7a5` el 22 de octubre. El relay anterior transfería los bytes directamente.
Además, existía un límite genérico de 25 MiB desde `e805253`; `180efb5` amplió a
100 MiB únicamente PDF. Ambas decisiones introdujeron restricciones de nuestra
integración que no corresponden a un transporte genérico.

El log del caso no conserva el error interno del decodificador ni los bytes del
sticker; no permite afirmar si era animado o estaba dañado. Las pruebas nuevas
incluyen WebP estático y animado generados de forma sintética, sin usar archivos
del cliente.

## Comportamiento corregido

- **WhatsApp → Chatwoot:** conservar bytes, MIME y nombre originales sin abrir,
  convertir o decodificar cada imagen/documento. No existe una lista local de
  extensiones que deban habilitarse una por una.
- La descarga desde Meta tiene un límite declarado y contado por bloques; las
  respuestas y buffers se cierran. Multipart se transmite por bloques para no
  construir una segunda copia completa del cuerpo de subida.
- Se mantienen IDs de cuenta/mensaje/adjunto y la lectura del objeto original con
  tamaño real y SHA-256 idénticos. Un MIME genérico, una clasificación `file` o
  un tamaño informativo diferente no bloquean un archivo cuyos bytes se han
  comprobado. Un 200 con HTML u otros bytes diferentes sigue fallando.
- **Chatwoot → WhatsApp:** todos los formatos usan el mismo transporte temporal
  y multipart por bloques. PDF, Office y otros documentos comparten el límite
  de documentos; no hay un filtro exclusivo de cabecera PDF. Formatos que Meta
  no admita producirán su rechazo real, sin una lista local adicional.
- WebP se envía con payload WhatsApp `sticker`, conservando sus bytes y animación,
  en lugar de pasar por la preparación de JPEG/PNG. Solo se inspeccionan flags de
  contenedor para distinguir el límite de sticker estático/animado, sin decodificar.
- Se conservan la adaptación de fotos grandes al tamaño permitido por Meta y
  la normalización de audio del asesor. M4A `audio/x-m4a` se normaliza a
  `audio/mp4` sin convertir sus bytes. La conversión necesaria de audio tiene
  un timeout de 15 segundos.
- El envío usa el tipo y nombre realmente subidos, incluidos los cambios de
  formato durante la preparación. El texto que acompaña a lotes compuestos
  únicamente por audio/stickers se envía una vez aparte, porque esos payloads
  no admiten caption.
- Las advertencias privadas dicen **sticker, imagen, video, audio o documento**,
  según corresponda. Un sticker no se presenta como un documento importante
  faltante; se pide aclaración por escrito solo si era necesario para entenderlo.

No se reintentan POST de envío ni se reproducen partes ya aceptadas de un lote.
Se mantienen la ventana original de WhatsApp y el control humano. El cambio es
genérico por BUSINESS_ID y no modifica precios, catálogos, Gemini ni el plan
documental de modelos de respaldo.

## Límites que siguen existiendo

El objetivo es transferir archivos soportados por los servicios sin barreras
artificiales del relay. La Cloud API de WhatsApp y Chatwoot siguen decidiendo
qué formatos y características aceptan. El código centraliza los techos de Meta:
imagen 5 MiB, audio/video 16 MiB, documento 100 MiB y sticker WebP estático
100 KiB/animado 500 KiB. Los stickers deben cumplir también las características
del proveedor, como dimensiones admitidas. Un contenedor WebP no identificable
localmente se deja al proveedor dentro del techo de 500 KiB.

WebP se trata como sticker porque es su ruta nativa en la Cloud API; una foto
WebP grande u otros formatos no admitidos pueden requerir preparación adicional.
GIF u otras imágenes fuera de JPEG/PNG/WebP se transmiten como documentos sin
alterar bytes y Meta puede rechazarlos. No se promete aceptar cualquier formato
que la aplicación móvil de WhatsApp permita.

Comprobar bytes idénticos demuestra integridad de transporte, no reproducción
en todo navegador. Se mantiene la política previa de **transcripción de notas de
voz entrantes** en handoff/atención humana mientras su reproducción no esté
verificada; esta corrección no reactiva audios no verificados.

Referencias del SDK oficial de WhatsApp consultadas:
[stickers](https://github.com/WhatsApp/WhatsApp-Nodejs-SDK/blob/58ca3d5fceea604e18393734578d9a7944a37b15/website/docs/api-reference/messages/sticker.md),
[mensajes y medios](https://github.com/WhatsApp/WhatsApp-Nodejs-SDK/tree/58ca3d5fceea604e18393734578d9a7944a37b15/website/docs/api-reference/messages).
Las páginas web de Meta no fueron accesibles desde este entorno; los rechazos
reales del proveedor siguen siendo la autoridad sobre soporte vigente.

## Configuración y despliegue

Aplica a proyectos nuevos y existentes sin SQL, nuevas credenciales ni variables
obligatorias. Bootstrap y upgrade mantienen su contrato. La dependencia
`requests-toolbelt==1.0.0` ya estaba incluida.

- `CHATWOOT_MAX_ATTACHMENT_BYTES`: default 104857600 (100 MiB); se respeta un
  límite menor configurado explícitamente. Las descargas de Meta y las salientes
  tienen techo de 100 MiB.
- `CHATWOOT_MAX_DOCUMENT_BYTES`: default/máximo 104857600 (100 MiB), ahora para
  todos los documentos salientes, no solo PDF. También puede reducirse.
- El transporte saliente necesita espacio temporal de hasta 100 MiB por
  transferencia concurrente. El entrante sigue ofreciendo bytes en memoria al
  contrato existente, pero con descarga acotada; no se añade un volumen persistente.

Revisar contra `42-actualizacion-de-precios`, base `019cc21`. Después de aprobar,
actualizar web/RQ del negocio al mismo SHA; no requiere redeploy de Chatwoot web,
Sidekiq, Supabase o Redis. No vaciar colas ni repetir mensajes históricos.

En el chat de pruebas confirmado por el operador, comprobar ambos sentidos con
JPEG/PNG, WebP estático/animado, PDF y documento Office; incluir un documento
mayor de 25 MiB y menor de 100 MiB y un lote mixto. Confirmar recepción/apertura,
SHA del deployment y eventos correlacionados. Una prueba forzada solo en entorno
de pruebas debe producir una nota privada que identifique sticker. HTTP 200 y
estado `sent` no sustituyen la comprobación en WhatsApp/Chatwoot.

Reversión: restaurar `019cc21` en web/RQ conservando datos y configuración;
reintroduce el filtro de decodificación y el límite especial de PDF. No restaurar
credenciales revocadas ni reiniciar bases o colas. Los jobs pendientes conservan
el contrato y no se reenvían automáticamente los ya fallidos.

## Validación y limitación de la suite general

**415 pruebas relacionadas aprobadas**, con proveedores simulados, contenido
sintético y codificación real de WebP estático/animado en las pruebas. Incluyen
cuatro negocios aislados, archivos de 26 MiB, hash idéntico, metadata genérica,
headers ausentes/incorrectos, límites de proveedor, notas privadas por tipo,
payload sticker, texto acompañante, recursos cerrados y lotes parciales sin replay.

La suite completa terminó con **788 aprobadas, tres omitidas y un fallo ya
existente**. Las omitidas necesitan binarios PostgreSQL ausentes. El fallo de
`test_memos_instruction_is_available_to_the_selected_deployment` exige una
cuenta anterior que ya no está en la instrucción comercial actualizada desde el
dashboard. Se reprodujo aislando el test y la instrucción de `019cc21`, sin estos
cambios de archivos. No se modifica la instrucción ni se elimina esa prueba en
este PR. Hay tres avisos de deprecación existentes.

La integridad de transferencia se probó localmente. Recepción y apertura reales
de ambos sentidos en producción quedan pendientes del despliegue aprobado y de
la prueba controlada del operador.
