# Revisión de inferencia Gemini: 27 de octubre de 2026

## Alcance y estado observado

Análisis del archivo aportado `logs.1793125253297.log`: 1.000 líneas que abarcan
aproximadamente **11:58–13:19, America/Bogota**, del 27 de octubre. Los eventos
identifican únicamente **Tanaka**, ejecutando
`64384ff5bbe1cb8ae563ac6753e3a07e7676a3a3`, posterior al merge del PR #138.
Es una muestra histórica limitada, no una consulta de salud en tiempo real ni
una evaluación de Memo's, Velvet o todas las conversaciones del día.

Estado de la muestra: **inferencia degradada, con derivación humana efectiva**.
No hay evidencia en este archivo de caída general de Supabase, Meta o Chatwoot.
Los dos intentos públicos de consultar salud desde el entorno de investigación
fallaron con `ProxyError`; eso limita esta revisión y **no demuestra** que los
servicios de producción estén caídos.

No se ha modificado producción ni se ha enviado ningún mensaje de prueba a
clientes. No se publican teléfonos, contenido de clientes, secretos o URLs
firmadas en este informe.

## Resultados de los logs

| Hallazgo | Evidencia y límite |
| --- | --- |
| Tres fallos de inferencia | Tres contactos distintos. Las seis coincidencias de texto no son seis fallos: cada caso aparece como error y como razón de derivación. |
| Dos rechazos temporales de Google | HTTP **503 `UNAVAILABLE`**, con indicación de alta demanda del modelo, alrededor de las **12:04 y 12:22**. No aparecen errores de Gemini por cuota agotada, 429 o autenticación en esta muestra. |
| Una espera excesiva | `rq.timeouts.JobTimeoutException` de **180 segundos** durante una lectura HTTP del SDK de Google. El job termina después de **185,61 segundos**. |
| Tres derivaciones creadas | Eventos `HANDOFF_CREATED` para los casos **607, 610 y 615**, a las **12:04:37, 12:22:53 y 13:08:17**, respectivamente. Confirman creación del caso, no que un asesor ya lo haya atendido. |
| Bloqueo vencido en el tercer caso | Después de crear el handoff, se registra `LockNotOwnedError / conversation_lock_unavailable` a las **13:08:19**. Es el mismo caso lento, no otro contacto afectado. |
| Conectividad con base de datos | 273 peticiones a Supabase correctas: **160 GET200, 44 PATCH200 y 69 POST201**. |
| Meta y Chatwoot siguen respondiendo | 13 POST a Meta con HTTP200 y actividad de respuestas de asesores en Chatwoot. La aceptación HTTP no prueba entrega al dispositivo. |

Se identifican cuatro mensajes entrantes: tres intentaron inferencia y fallaron;
uno se procesó con atención humana ya activa. Los cuatro jobs tienen duraciones
de **19,81; 12,22; 4,47 y 185,61 segundos**. Los mensajes de éxito del logger RQ
están duplicados: no se cuentan como ocho jobs. La muestra no permite calcular
una tasa de fallo global o afirmar que toda llamada del día falló.

Para correlacionar horarios se priorizan `observed_at_colombia` y los timestamps
originales de los eventos. Las marcas externas de captura de Railway pueden
estar retrasadas, intercaladas o fuera de orden. Los 19 marcadores de arranque
del código no demuestran 19 despliegues: también aparecen al ejecutar jobs.

## Causa técnica y corrección preparada

Hay dos problemas distintos:

1. **Fallo transitorio del proveedor y clasificación incompatible con el SDK.**
   `google-genai` expone el estado HTTP mediante `APIError.code` y su payload
   mediante `APIError.details`. El clasificador anterior buscaba nombres de
   atributos de otros wrappers y no reconocía correctamente un `ServerError`
   real con 503. Se reprodujo con excepciones del SDK: la aplicación pasaba a
   handoff sin efectuar los reintentos transitorios previstos. No se confunde
   esta ausencia de reintentos de aplicación con la política interna del SDK.
2. **Llamada de red sin timeout explícito adecuado.** El cliente Gemini anterior
   no fijaba un timeout HTTP; una llamada pudo agotar los 180 segundos de RQ.
   El manejador genérico de inferencia absorbió esa excepción y continuó con la
   derivación. Al salir, el lease del lock de conversación había vencido y su
   liberación produjo el segundo error. Un proveedor lento no debe depender del
   timeout del worker para activar la contingencia.

La corrección conserva el modelo, prompts, personalidad, precios, catálogos y
reglas comerciales. Reconoce las propiedades reales del SDK y los errores de
red HTTPX; los errores permanentes y créditos prepagados agotados no se
convierten en reintentos indiscriminados.

El runtime compartido `run_gemini` aplica por defecto:

- **20 segundos por petición**, expresados en milisegundos en `HttpOptions`.
- **60 segundos de presupuesto por operación**, incluyendo la espera del
  semáforo y el backoff. Ningún intento nuevo empieza después de agotar ese
  presupuesto; los timeouts se recortan al tiempo disponible.
- **Un intento en el SDK** y hasta **tres intentos de aplicación** para fallos
  transitorios, evitando multiplicar dos bucles de reintento.
- Adquisición del semáforo con espera limitada y liberación antes del backoff.
  El semáforo es local al proceso; no es una cuota global entre despliegues.

Los timeouts de HTTPX se aplican a fases de conexión/lectura, no constituyen una
interrupción garantizada en un instante absoluto de reloj mientras una petición
está en curso. Por ello, los valores anteriores son controles de petición y
presupuesto de inicio de intentos, **no una garantía absoluta de 20/60 segundos
de extremo a extremo**. Tampoco garantizan que Google deje de responder 503.

Un audio puede necesitar dos operaciones separadas: transcripción y respuesta,
con presupuestos nominales de hasta **120 segundos más el resto del proceso**
con los valores por defecto. Deben considerarse también la descarga, persistencia,
esperas de bloqueo y handoff antes de reducir los límites del worker o elevar
estos presupuestos. La corrección no aumenta automáticamente el timeout de RQ
ni el TTL del lock.

Las opciones `GEMINI_REQUEST_TIMEOUT_SECONDS` y
`GEMINI_OPERATION_TIMEOUT_SECONDS` son opcionales y tienen los defaults
anteriores; no es necesario añadir variables para aplicar el cambio. Cada
negocio mantiene sus credenciales, número, inbox, base de datos y cola propios.

## Hallazgos separados de la inferencia

- Dos adjuntos salientes se detienen por `attachment_too_large` con Storage
  HTTP200. Es el límite de tamaño de esta integración, no una avería de Gemini.
  Deben revisarse tamaño y tipo del archivo concreto antes de cambiar límites.
- Se ven otras subidas de adjuntos aceptadas por Meta. El inicio del archivo
  está cortado y contiene un evento con diez adjuntos; no permite contar todos
  los archivos de la conversación ni demostrar recepción de cada uno.
- Los 15 rechazos de webhook por `scope_mismatch` corresponden a protección
  entre inboxes. No se debilitan autenticación ni aislamiento para corregir la
  inferencia.

## Instalaciones nuevas y existentes

La corrección es genérica por `BUSINESS_ID`. No introduce tablas, columnas,
políticas, nuevas dependencias o variables obligatorias; instalación con
`supabase/bootstrap.sql` y actualización con
`supabase/upgrade_existing_brand.sql` conservan el mismo contrato. No requiere
ejecutar SQL ni modificar Supabase, Redis, PostgreSQL, Chatwoot o integraciones
de Meta.

## Validación y despliegue después de revisión

Suite completa ejecutada con el wrapper que excluye la configuración real de
negocios del proceso de pruebas:

```text
.venv/bin/python /workspace/.cloud/whatsapp-bot-shared/test.py --tb=short
645 passed, 3 skipped, 3 warnings
```

Las tres pruebas omitidas necesitan los binarios de PostgreSQL, ausentes en este
entorno; los tres avisos de deprecación ya existían. No hay cambios SQL en esta
corrección. Se verificaron excepciones reales del SDK fijado, un transporte HTTP
simulado del cliente real, unidades de timeout, límites de presupuesto, espera
y liberación del semáforo, backoff y rutas de texto/audio por marca. Se utilizan
datos sintéticos y proveedores simulados; esta preparación no es una prueba de
recuperación de Gemini en producción.

Después de revisar y aprobar el cambio:

1. Actualizar **solo web y los workers RQ del negocio seleccionado al mismo
   SHA**, mediante su procedimiento habitual. No desplegar todos los servicios,
   mover credenciales entre marcas, tocar Sidekiq o vaciar Redis.
2. Comparar el SHA real de web y de cada worker, y probar una conversación
   sintética autorizada. Usar los IDs de mensaje y eventos para correlacionar
   la entrada, respuesta o derivación.
3. Observar `[GEMINI RETRY]`, `[GEMINI RETRY EXHAUSTED]`,
   `[GEMINI BUDGET EXHAUSTED]`,
   latencia y creación efectiva de handoffs. Un 503 recuperable debe poder
   recuperarse sin handoff innecesario; agotados los intentos, debe conservarse
   la derivación humana sin duplicar respuestas.
4. Comprobar que no reaparecen llamadas que agoten los **180 segundos** ni el
   error de lock vencido posterior. Si continúan, conservar evidencia del tipo
   de excepción y tiempos antes de aumentar límites o concurrencia.
5. No repetir colas fallidas en bloque, reenviar mensajes a clientes ni
   reactivar automáticamente los casos que ya están bajo atención humana.
   Los casos existentes requieren el flujo normal del asesor.

Reversión del cliente: restaurar el SHA anterior aprobado en web y RQ del
negocio afectado, preservando configuración y colas; no requiere rollback SQL.
Esa reversión también reintroduciría la clasificación incompleta del SDK y la
ausencia de estos límites explícitos. No restaurar credenciales históricas para
revertir este código.
