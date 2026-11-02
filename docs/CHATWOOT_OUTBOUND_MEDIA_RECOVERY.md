# Adjuntos de asesor: archivo pendiente en Storage y fotos grandes

Actualización vigente del 30 de octubre: [relay genérico de archivos](GENERIC_MEDIA_RELAY.md).
La ampliación a 100 MiB ahora cubre documentos en general, no solo PDF.

Actualización del 28 de octubre: [PDF de hasta 100 MiB y diagnóstico de Gemini](HEALTHCHECK_2026_10_28.md).
El registro siguiente describe el cambio del 26 de octubre; la actualización
posterior añade una dependencia y una configuración opcional para PDF salientes.

## Evidencia del 26 de octubre de 2026

La instancia de Chatwoot usa `v4.16.2-ce`; el bot de la prueba ejecutaba
`1cc02b52c2ca17b29c469ae5a9b3ff8a87ec69c5`. La prueba posterior al nuevo
despliegue de Chatwoot web y Sidekiq confirmó la recepción de imágenes desde
WhatsApp, pero no completó la validación del sentido asesor → WhatsApp.

Horas de Colombia (`America/Bogota`):

| Mensaje | Evidencia | Conclusión |
| --- | --- | --- |
| Video 4104, 10.111.504 bytes | Creado a las 16:15:11.892. A las 16:15:15, Chatwoot respondió 302 y Storage respondió 404 `NoSuchKey`. Los trabajos de análisis del mismo blob se encolaron a las 16:15:20 y lo descargaron correctamente a las 16:15:31–32. | El webhook llegó antes de que estuviera disponible el objeto. El fallo ocurrió antes de subirlo o enviar un mensaje a Meta. |
| Foto 4105, 5.917.241 bytes | Descarga completa; Meta rechazó la subida con HTTP 400. | El rechazo está confirmado. Además, la imagen supera el límite de tamaño de imágenes de WhatsApp. El log no contiene el código ni el cuerpo del error para atribuir ese 400 exclusivamente al tamaño. |

Los dos mensajes se marcaron como `failed` en Chatwoot. Después aparecen
reintentos de `SendReplyJob` y un cambio de `failed` a `sent` para la foto: ese
cambio por sí solo no demuestra un envío a Meta ni una entrega al cliente.

El código oficial explica la carrera:

- [Message de Chatwoot 4.16.2](https://github.com/chatwoot/chatwoot/blob/v4.16.2/app/models/message.rb#L324)
  dispara `MESSAGE_CREATED` antes de llamar a `send_reply`. El propio
  [comentario de `send_reply`](https://github.com/chatwoot/chatwoot/blob/v4.16.2/app/models/message.rb#L397)
  reconoce que la subida de Active Storage termina después del commit y retrasa
  dos segundos únicamente `SendReplyJob`.
- [WebhookListener](https://github.com/chatwoot/chatwoot/blob/v4.16.2/app/listeners/webhook_listener.rb#L25)
  encola el webhook sin esa espera.
- [Rails Active Storage 7.1.5.2](https://github.com/rails/rails/blob/v7.1.5.2/activestorage/lib/active_storage/attached/model.rb#L130)
  guarda la asociación y sube los bytes en callbacks distintos.

La ausencia de `ffprobe` en el worker de Chatwoot explica que omita el análisis
del video; no explica el 404 anterior del bot. Los 401/403 hacia webhooks de otros
negocios tampoco explican el fallo del adjunto de Tanaka. No se modifican esos
filtros ni sus secretos para reparar este caso.

## Cambio del cliente del bot

- Para mensajes con timestamp original válido y reciente (hasta 120 segundos;
  tolerancia de reloj de 30 segundos hacia el futuro), solo un **404 `NoSuchKey`
  del Storage externo** permite volver a descargar. Hay como máximo cuatro
  reintentos GET, con esperas de 1, 2, 4 y 8 segundos. Un presupuesto nominal de
  20 segundos restringe los reintentos y sus timeouts; el tiempo de red no tiene
  una garantía absoluta de 20 segundos de extremo a extremo.
- Cada reintento parte del enlace original validado de Chatwoot y obtiene una
  redirección nueva. Las respuestas se cierran antes de esperar. El bucket no
  recibe credenciales de Chatwoot. Errores de firma, permisos, objetos históricos
  o mensajes sin fecha no entran en este mecanismo.
- Se comprueba la ventana original de WhatsApp antes de esperar y antes de
  contactar a Meta. La espera no amplía la ventana de 24 horas.
- Las imágenes JPEG/PNG que ya caben conservan sus bytes. Las mayores de 5 MiB
  se adaptan en la copia saliente a JPEG, manteniendo proporciones, con un máximo
  de 2048 píxeles y una alternativa de 1280. La conversión tiene un plazo de
  15 segundos y un tamaño máximo de salida. El original en Chatwoot se conserva.
- Un adjunto sin URL original falla antes de enviar el primer archivo. Una
  miniatura no sustituye al original. No se imprimen payloads de adjuntos,
  contenido, secretos o URLs firmadas. Los errores de Meta conservan códigos
  numéricos cuando están disponibles, sin publicar el cuerpo del proveedor.
- La aceptación síncrona se refleja como `sent`, no como `delivered`. Un fallo
  de adjunto actualiza el estado y añade una nota privada al asesor.

No se reintentan POST de subida a Meta ni envíos de mensajes. Un fallo en un
mensaje con varios archivos no vuelve a enviar las partes ya aceptadas.
El consumidor aún no sincroniza recibos asíncronos de Meta con Chatwoot; `sent`
representa aceptación, no confirmación de entrega ni de lectura.

## Instalaciones nuevas y existentes

Aplica al backend compartido de cualquier `BUSINESS_ID`, con las credenciales,
cuentas e inboxes propios de su despliegue. No requiere SQL, migraciones, nuevas
dependencias o variables. Utiliza el ffmpeg ya incluido para audio. Bootstrap y
upgrade conservan su contrato actual; ninguna tabla o política cambia.

## Revisión, despliegue y verificación

1. Revisar este cambio contra `42-actualizacion-de-precios`, donde está integrado
   el PR #137. Esta preparación no despliega ni reinicia producción.
2. Tras aprobarlo, actualizar únicamente los servicios web y RQ del negocio
   elegido al mismo SHA, mediante el procedimiento habitual de ese negocio.
   No requiere volver a desplegar Chatwoot web o Sidekiq ni modificar Storage.
   No usar `Deploy all` ni reiniciar bases de datos o colas.
3. No vaciar Redis, repetir trabajos fallidos en bloque ni usar `Retry all`.
   Los trabajos todavía pendientes conservan compatibilidad; la espera solo se
   aplica a mensajes recientes. Los trabajos ya fallidos no se reproducen solos.
4. En el chat de pruebas que indique el operador, enviar archivos **nuevos** como
   respuesta pública del asesor: foto pequeña, JPEG/PNG mayor de 5 MiB y MP4
   compatible con WhatsApp por debajo de su límite de video. Comprobar que llegan
   y abren en WhatsApp. Después comprobar cliente → Chatwoot.
5. Correlacionar ID de mensaje, `[CHATWOOT ATTACHMENT WAIT]`,
   `[CHATWOOT ATTACHMENT]`, códigos del proveedor y SHA de web/RQ. Un `NoSuchKey`
   temporal debe acabar en una única subida y envío; un rechazo permanente debe
   producir estado fallido y nota privada. El éxito del job o `sent` no sustituye
   la comprobación de recepción del archivo.

El botón **Reintentar** de Chatwoot no es una recuperación fiable de esta
integración API: su
[controlador](https://github.com/chatwoot/chatwoot/blob/v4.16.2/app/controllers/api/v1/accounts/conversations/messages_controller.rb#L28)
cambia el estado y encola `SendReplyJob`; para `Channel::Api`, ese
[job](https://github.com/chatwoot/chatwoot/blob/v4.16.2/app/jobs/send_reply_job.rb#L15)
usa el servicio de notificaciones por email. Este backend reenvía eventos
`message_created`, no cada `message_updated`. No se habilitan reenvíos por todos
los cambios de estado, porque podrían duplicar mensajes ya aceptados. Después
de verificar que un archivo falló antes de llegar a Meta, el operador puede
enviarlo como un mensaje nuevo.

Reversión del cliente: restaurar `1cc02b5` en web y RQ del negocio afectado,
manteniendo configuración actual y Redis. Reintroduce la carrera y la ausencia
de adaptación de fotos; no requiere restaurar credenciales antiguas ni tocar
Chatwoot, Sidekiq, Supabase o migraciones.

Validación local: 609 pruebas aprobadas; tres pruebas de integración PostgreSQL
omitidas por ausencia de sus binarios y tres avisos de deprecación existentes.
Las pruebas usan proveedores simulados y medios sintéticos, incluida conversión
y decodificación reales con ffmpeg. Producción queda pendiente de la revisión,
despliegue y prueba de ambos sentidos descritos arriba.
