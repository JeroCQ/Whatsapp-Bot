# Audio e imágenes en derivaciones y atención humana

La investigación y corrección del **26 de octubre de 2026** (webhook antes de
terminar la subida y fotos grandes del asesor) está en
[CHATWOOT_OUTBOUND_MEDIA_RECOVERY.md](CHATWOOT_OUTBOUND_MEDIA_RECOVERY.md).
Las secciones siguientes conservan la evidencia de los incidentes anteriores.

## Incidente confirmado del 23 de octubre de 2026

La investigación actual utiliza **únicamente**
`logs.1792762557129.log.txt` para el incidente. Web y worker arrancaron con
**3bdc8c66b7d33e53c49478878e0aef6acd76aff4**, el merge del PR **#136**;
ese PR sí está integrado en `42-actualizacion-de-precios`. No falta repetirlo.

- **09:11 y 09:25 de Bogotá:** un adjunto del asesor intenta salir de Chatwoot
  hacia WhatsApp. La URL de Chatwoot responde **302** y su destino de almacenamiento
  `chatwoot-attachments-sxl4mu.t3.storageapi.dev` responde **403**. El fallo ocurre
  antes de subir bytes a Meta; no es un rechazo del formato por WhatsApp.
- **09:12:** Meta entrega una imagen JPEG de **377.994 bytes** con HTTP 200.
  La validación local pasa. Chatwoot devuelve **422 al POST multipart**;
  la comprobación posterior del objeto ni siquiera comienza. El webhook del
  mensaje entrante **3662** llegó antes del 422. Por ello, el 422 no demuestra
  que el mensaje no se guardara y **no habilita un reenvío automático**.
- Los 403 de `/chatwoot-webhook` por otros inboxes son rechazos de aislamiento
  y son distintos del 403 del bucket. No se deben quitar esas comprobaciones.
- Dos respuestas de texto de las 09:02 se bloquearon porque no había un
  timestamp original válido para la ventana de Meta. Son otro problema,
  distinto de la subida y de la descarga de archivos. No se amplía esa ventana
  usando la hora del asesor o la hora de ejecución del job.

El contrato multipart vigente coincide con Chatwoot: `attachments[]`,
`message_type=incoming` y `private=false` para archivos del cliente.
Su [controlador de mensajes en v4.4.0](https://github.com/chatwoot/chatwoot/blob/545453537f4d13bcbf846621aae3051d9815d1e9/app/controllers/api/v1/accounts/conversations/messages_controller.rb#L8)
convierte cualquier excepción de creación en
[HTTP 422](https://github.com/chatwoot/chatwoot/blob/545453537f4d13bcbf846621aae3051d9815d1e9/app/controllers/concerns/request_exception_handler.rb#L34).
Rails realiza la [subida a ActiveStorage después del commit](https://github.com/rails/rails/blob/v7.1.5.1/activestorage/lib/active_storage/attached/model.rb#L132).
Una excepción de almacenamiento puede dejar un mensaje guardado con un archivo
inaccesible y devolver 422. Esto explica un mecanismo posible, pero el log
no contiene el cuerpo del 422 ni identifica la excepción real del servidor.

### Corrección del cliente y diagnóstico

Se corrige una incompatibilidad adicional comprobada: el
[modelo Attachment de Chatwoot v4.4.0](https://github.com/chatwoot/chatwoot/blob/545453537f4d13bcbf846621aae3051d9815d1e9/app/models/attachment.rb#L100)
omite `content_type` de su respuesta. También lo omiten v3.16 y v4.3.
Ese campo deja de ser obligatorio en la metadata. Cuando viene, se valida;
el MIME HTTP del objeto, su tamaño y la comparación exacta de bytes siguen
siendo obligatorios. La falta de ese campo no explica por sí sola el POST 422.

Los fallos registran un código de almacenamiento conocido, si existe, y el
request ID del servidor. La descarga del asesor distingue origen Chatwoot,
almacenamiento y subida a Meta, conserva el estado HTTP y cierra las respuestas
también al fallar. Los saltos al bucket usan una sesión sin credenciales API ni
autenticación heredada de netrc. No se imprimen cuerpos, URLs firmadas ni claves.
No se añaden SQL, variables obligatorias o dependencias; aplica a negocios nuevos
y existentes, conservando sus credenciales y aislamiento.

### Reparación que debe comprobarse en el Chatwoot de producción

El repositorio del bot no contiene el despliegue ni las credenciales de Storage
de Chatwoot. La corrección del cliente **no puede reparar permisos o claves del
bucket**. No se ha cambiado su configuración ni se ha desplegado esta corrección.

1. En Railway, abrir **el servicio de Chatwoot**, no el bot, y consultar sus logs
   a las **14:12:30 UTC / 09:12:30 de Bogotá del 23 de octubre**. Correlacionar
   el request ID cuando esté disponible. Conservar únicamente el nombre del error,
   sin publicar credenciales ni URLs firmadas.
2. Revisar la configuración del servicio seleccionado por `ACTIVE_STORAGE_SERVICE`
   según el [storage.yml de la versión instalada](https://github.com/chatwoot/chatwoot/blob/545453537f4d13bcbf846621aae3051d9815d1e9/config/storage.yml).
   `s3_compatible` utiliza `STORAGE_ACCESS_KEY_ID`, `STORAGE_SECRET_ACCESS_KEY`,
   `STORAGE_REGION`, `STORAGE_BUCKET_NAME`, `STORAGE_ENDPOINT` y
   `STORAGE_FORCE_PATH_STYLE`. `amazon` utiliza las variables `AWS_*` y
   `S3_BUCKET_NAME`. Son variables de **Chatwoot**, no del bot ni de Supabase.
   Web y Sidekiq deben usar la configuración del mismo almacenamiento.
3. Resolver el error concreto con los valores vigentes del bucket: `InvalidAccessKeyId`
   exige revisar la clave activa y su pareja secreta; `AccessDenied`/`Forbidden`
   exige comprobar permisos de lectura y escritura y el bucket seleccionado;
   `SignatureDoesNotMatch` exige revisar pareja de claves, endpoint, región y
   configuración de firma; `ExpiredToken` exige renovar las credenciales temporales;
   `RequestTimeTooSkewed` exige revisar el reloj del servidor. Estos son criterios
   de diagnóstico, no prueba de que haya ocurrido uno de ellos. No cambiar valores
   por conjetura, ni hacer público el bucket, ni eliminar objetos o conversaciones.
4. Después de reparar Storage y de revisar el cambio del bot, actualizar web y
   todos sus workers al mismo SHA. En un chat sintético, comprobar imagen de cliente
   hacia asesor **y** archivo de asesor hacia WhatsApp, abrir los archivos en Chatwoot
   y verificar ausencia de 422/403. Repetir el handoff por voz: conserva la
   transcripción identificada y no adjunta audio sin reproducción verificada.
5. No repetir de forma masiva trabajos fallidos o POST 422: el mensaje puede existir.
   Revisar los IDs concretos. Los adjuntos históricos inaccesibles no quedan
   reparados automáticamente por un cambio de código.

Para revertir **solo este cambio del cliente**, desplegar el SHA previo **3bdc8c6**
en web y workers, sin vaciar Redis. No requiere revertir SQL. Esa reversión
reintroduce la incompatibilidad de MIME y no corrige un fallo del bucket.

Validación de esta corrección: **573 pruebas aprobadas**, ninguna omitida,
incluidas instalación nueva y upgrade reales en PostgreSQL; tres avisos de
deprecación existentes. Se reprodujeron respuestas oficiales sin MIME,
rechazos 422 con y sin clase SDK, XML 403, lectura interrumpida, redirecciones,
límites, aislamiento y ausencia de reenvíos. Se utilizaron proveedores simulados
y archivos sintéticos. No se ha comprobado producción ni la interfaz del asesor.

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
- Desde la [corrección de relay del 30 de octubre](GENERIC_MEDIA_RELAY.md), un
  archivo entrante no requiere firma/decodificación de una imagen local. La subida
  exige una respuesta con IDs de mensaje/adjunto y cuenta consistentes; la lectura
  posterior de `data_url` debe devolver **los mismos bytes**. Metadatos de MIME,
  clasificación o tamaño distintos quedan como diagnóstico si el contenido es
  idéntico. La verificación usa HTTPS,
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
   PR ni de un log filtrado. El PR #136 ya está fusionado; la corrección del
   23 de octubre necesita una revisión nueva.
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
La suite del PR #136 terminó con **545 pruebas aprobadas**, incluidas las migraciones
reales de instalación nueva y upgrade en PostgreSQL, sin pruebas omitidas.
La prueba de visualización real sigue siendo necesaria tras desplegar.
