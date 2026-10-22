# Audio e imágenes en derivaciones y atención humana

## Evidencia del caso del 22 de octubre de 2026

El archivo `logs.1792685649801.log.txt` corresponde a **Velvet**. Registra una nota
de voz Ogg/Opus a las **09:44 de Bogotá**, la derivación a las **09:44:57** y una
imagen JPEG durante atención humana a las **10:38:43**. Los jobs terminan como
completados, pero el archivo está filtrado por contacto y no contiene el SHA,
la respuesta de subida de Chatwoot, la conversión de audio ni la lectura de sus
objetos almacenados. No demuestra que esos adjuntos pudieran abrirse.

El PR **#135**, merge **9579610**, se integró a `42-actualizacion-de-precios` a las
**12:02:20 de Bogotá**, después de ambos eventos. No se puede atribuir a ese merge
un fallo ocurrido antes. La rama de esta corrección parte de ese merge.

El historial muestra que el envío multipart existe desde `da17ca5` (julio) y la
conversión Ogg→MP3 con fallback al original desde `6e72bb7` (agosto). `1d51b5f`
reutilizó el conversor para audio de asesor hacia Meta; no cambió los bytes de
WhatsApp hacia Chatwoot. El PR #135 modificó el tipo de las notas privadas, pero
conservó descarga, conversión, bytes y MIME de los adjuntos. La imagen posterior
al handoff sigue siendo un mensaje entrante público del cliente.

Por tanto, el log no identifica qué cambio de servidor o configuración produjo
el problema. Que fallen tanto audio como imagen justifica revisar también las
URLs y almacenamiento de Chatwoot, además del formato de audio. No se inspeccionó
el Chatwoot real: el entorno compartido carece de sus credenciales y del archivo
publicado. No se modificó producción.

## Comportamiento corregido

- El resumen identifica explícitamente la **nota de voz**, su **transcripción
  automática completa** y que esa transcripción motivó la derivación. El historial
  distingue voz transcrita de texto escrito. El mensaje entrante se actualiza en
  su propia fila, conservando timestamp y correlación; no se cuenta dos veces.
- La causa de audio se conserva como metadata de un evento interno, para que una
  creación de caso recuperada después de un fallo también conserve ese contexto.
  No se infiere la causa a partir de un audio cercano ni se reutiliza tras un cierre.
- Las rutas de handoff y atención humana envían **solo la transcripción** de las
  notas de voz. No adjuntan audio cuya reproducción en el cliente del asesor no
  se ha verificado. Si la transcripción falla, el asesor recibe un aviso explícito
  para solicitar el contenido por escrito; no se inventa ni se pierde silenciosamente.
- El helper de conversión deja de adjuntar el Ogg original si falla ffmpeg. El
  conversor usado para audio del asesor hacia WhatsApp sigue disponible.
- Una imagen debe tener firma y decodificación válidas antes de subirla. La subida
  exige una respuesta de mensaje/adjunto consistente y la lectura posterior de
  `data_url` debe devolver el tipo y **los mismos bytes**. La verificación usa HTTPS,
  límites de tamaño, timeout y redirecciones acotadas, sin enviar credenciales API
  de Chatwoot a los objetos almacenados.
- Si falla la descarga, subida o lectura del objeto, se registra
  `CHATWOOT_MEDIA_DELIVERY_FAILED` y el asesor recibe una **nota privada**. La
  conversación conserva el vínculo y control humano. Un fallo durante atención
  humana marca el evento entrante como fallido en vez de darlo por entregado.
  No dispara otra derivación ni un aviso automático al cliente.

La comprobación del objeto demuestra acceso desde el servicio y coincidencia del
contenido; **no garantiza el funcionamiento del navegador o la app del asesor**.
Si un POST creó el adjunto pero su lectura no se pudo verificar, no se borra ni se
vuelve a subir automáticamente: podría haberse recibido. El fallo se deriva a
revisión para evitar duplicados. Esta corrección no repara adjuntos históricos.

Se aplica a instalaciones nuevas y existentes de cualquier negocio. Utiliza
metadata JSON existente y `CHATWOOT_MAX_ATTACHMENT_BYTES`; **no añade SQL ni
credenciales compartidas**. No cambia precios, pagos, catálogo o reglas comerciales.

## Comprobación posterior en el despliegue aislado

1. Tras revisar el cambio, actualizar web y worker al mismo SHA y comprobar
   `/health` y el commit de los workers. No inferir el SHA a partir del estado del
   PR ni de un log filtrado. El PR #135 ya está fusionado; este cambio necesita una
   revisión posterior.
2. Con datos sintéticos, provocar un handoff mediante voz. El asesor debe ver la
   transcripción identificada, completa y ligada al motivo; no debe aparecer un
   reproductor vacío. Repetir con audio recibido durante atención humana.
3. Enviar una imagen sintética al caso ya asignado. Comprobar
   `[CHATWOOT MEDIA] result=verified`, los IDs de mensaje/adjunto y que el asesor
   pueda visualizarla tanto en web como en la app que utiliza.
4. Si falla, consultar `verification_phase`, `reason`, estado HTTP del objeto y
   tamaño leído. No publicar el cuerpo del cliente, tokens ni URLs firmadas.
   `asset_http_error`, `asset_content_type_mismatch` o `asset_bytes_mismatch`
   distinguen problemas de lectura/objeto de una subida rechazada.
5. En el **Chatwoot que almacena los adjuntos**, revisar su URL pública, acceso a
   ActiveStorage, destino de redirección y configuración de su bucket. Confirmar
   que el objeto no responde con HTML de login/error ni 403/404, que el dominio y
   certificado son accesibles desde el dispositivo del asesor y que no hay fallos
   de Storage en el servidor. Revisar esa instalación; no cambiar el Supabase del
   bot o permisos de otras marcas por conjetura.

Las pruebas usan archivos sintéticos decodificables, proveedores simulados,
respuestas HTML/404, bytes alterados, límites y redirecciones sin credenciales.
La suite completa terminó con **545 pruebas aprobadas**, incluidas las migraciones
reales de instalación nueva y upgrade en PostgreSQL, sin pruebas omitidas.
La prueba de visualización real sigue siendo necesaria tras desplegar.
