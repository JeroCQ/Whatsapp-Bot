# Revisión conjunta de seguimientos, derivaciones y pagos

Este cambio se prepara para revisión hacia `42-actualizacion-de-precios`. No ejecuta
migraciones, merge ni despliegue de producción. El servicio afectado por la auditoría
es `Memos-Whatsapp-Bot`; cada marca conserva su Railway, Supabase, Redis, número Meta
e inbox Chatwoot propios. La misma implementación sirve a marcas futuras.

## Base y causas comprobadas

La referencia auditada fue `e283a74f5080ee6eaae555386e7fae33e5c3a0c6`, que ya incluye
el seguimiento personalizado/neutro de `c784d15`. Se conserva además `4337311`, la
corrección posterior para usuarios de WhatsApp identificados mediante BSUID.
Los cambios de pagos/recogida de `687547b` se incorporan a esta misma revisión.

## Cambios de los pedidos anteriores incluidos

- Seguimientos personalizados por negocio y fallback neutro: ya están en la rama
  de referencia y se conservan. Solo se reemplaza la antigua plantilla exacta que
  mencionaba panadería/postres en trabajos pendientes.
- Identidades WhatsApp con BSUID: se conservan sin convertirlas en números, con
  destinatario Meta correcto, contacto Chatwoot aislado y columnas `phone_number`
  de tipo text en instalación nueva y upgrade. Esto incluye el caso de perfiles
  con usuario cuya identidad recibida no es su número visible.
- Tanaka y Mochis/Velvet comparten la llave **@bold3187622894** como primera opción.
  Cada marca conserva su Bancolombia anterior como alternativa solicitada; Velvet
  conserva sus alternativas Nequi/Daviplata solo a solicitud. No se inventa titular
  o banco a partir de los nombres enmascarados del mensaje recibido.
- Datáfono únicamente en punto físico. Link de pago con **2,5% sobre el total**,
  desglose previo y enlace real confirmado; si falta, se deriva al equipo.
  La verificación del comprobante sigue siendo humana.
- Para ambas marcas: recogida sin domicilio en **Carrera 10 #47-31, barrio Troncal,
  cerca de la Base Aérea de Cali**, lunes–sábado **09:00–18:00**, e invitación a
  conocer la sede cuando el cliente objeta el costo del domicilio.
- **3025991292 y tanakasaludablecali@gmail.com son solo de Tanaka**. No se copian a
  Velvet. Se conservan precios, identidad, catálogo, cobertura, cortes de domicilio,
  anticipación de tortas y condiciones de prepago/contraentrega propias de cada marca.
- El entorno de desarrollo incorpora `requirements-dev.txt`, pruebas sintéticas y
  las instrucciones de instalación/inicio actualizadas en el borrador del entorno.
  Su publicación en ajustes es independiente del merge y de producción.

Las causas del backend eran:

- Cada offset se calculaba por separado y varios se trasladaban a la misma apertura.
  La ejecución no comprobaba el último contacto entre intentos distintos.
- El claim antiguo usaba contacto/etapa/intento sin secuencia ni negocio, y agotaba
  el permiso antes de saber si Meta había aceptado el envío.
- Un seguimiento intencionalmente vacío producía un mensaje de fallback.
- Se usaba la hora de procesar la cola como inicio de las 24 horas de WhatsApp.
- La ventana fija no distinguía contacto, operación ni fecha acordada. Memo's no
  atiende los domingos, aunque un texto de apertura podía sugerir lo contrario.
- “Precios al por mayor” no activaba el detector B2B. La selección automática de
  catálogo podía suprimir una derivación válida. Un fallo de Chatwoot reactivaba
  el bot y la respuesta al cliente precedía a la creación del caso.
- Los logs carecían de relación explícita entre entrada y respuesta. Las métricas
  por proximidad confundían seguimientos y no atendían lotes de mensajes.

## Comportamiento resultante

Una secuencia usa una referencia única y consolida aperturas coincidentes. Los
intentos restantes respetan una separación configurable; el worker vuelve a
comprobar calendario, token, conversación, preferencias, fecha acordada y último
contacto antes del POST. El ingreso invalida la secuencia antes de esperar en cola,
con deduplicación de eventos incluso si tienen el mismo segundo de Meta.

Redis reserva por negocio/número Meta/contacto/secuencia/etapa/intento. La reserva
puede recuperarse si vence antes del envío, con un máximo de tres reservas.
`sending` se persiste antes del POST; `sent` solo después de aceptación. Un rechazo
HTTP 4xx o timeout de conexión confirmado anterior al envío permite recuperación
acotada. Timeout de lectura, pérdida de conexión,
respuesta 5xx o caída durante el envío quedan ambiguos y no se reenvían a ciegas.
La separación se ancla a la aceptación; una caída durante el POST añade un margen
de 60 segundos para una solicitud en vuelo. Redis indisponible impide el envío.

`follow_up_message` ausente permite fallback neutro; vacío, whitespace o null
explícito cancelan. Rechazo, cierre, asesor y “yo te aviso” cancelan los anteriores.
La decisión de no contactar persiste entre agradecimientos; una solicitud de no
contacto requiere consentimiento explícito para reactivarse. Un agradecimiento
dentro de un pedido pendiente conserva continuidad si el modelo no la canceló.

Los calendarios viven en `src/clients/{BUSINESS_ID}/business_policy.json`, nunca en
ramas de código por marca. Memo's tiene contacto lunes–sábado 08:00–18:00 y operación
lunes–sábado 06:00–16:30, `America/Bogota`. Tanaka y Velvet tienen operación presencial
09:00–18:00 lunes–sábado; esto no cambia cortes de despacho, cobertura o anticipación.
Una fecha acordada se conserva en `follow_up_not_before`. Si cae fuera de la ventana
original de 24 horas, se omite el envío libre: no se mueve la fecha ni se inventa una
plantilla aprobada. La misma validación protege respuestas, archivos y avisos de cierre.

Las derivaciones B2B y otras solicitudes válidas tienen precedencia sobre catálogos.
Un catálogo simple disponible sigue siendo automático. El bot conserva la pausa si
falla Chatwoot; `handoff_deliveries` permite tres intentos acotados, recuperación tras
reinicio y revisión de resultados ambiguos. Una creación ambigua nunca abre un segundo
caso automáticamente. La respuesta al cliente informa que el caso sigue pendiente
si no pudo confirmarse su creación. Los avisos privados B2B se limitan a uno por caso.
`GET /api/pending-handoffs?client_name=<BUSINESS_ID>` usa la autenticación del dashboard.
La política de Velvet declara `standard_wholesale_self_service=true`: las cantidades
mayoristas dentro de su tarifario siguen atendiéndose automáticamente según su
instrucción aprobada; no se importa el criterio mayorista de Memo's o Tanaka.

“Bloque de cinco libras” sin una presentación aprobada requiere validación humana;
no significa inexistencia ni autoriza inventar precio. Pedir una cantidad en libras
por sí sola no equivale a pedir un bloque o tarifa mayorista.

Cada registro incluye negocio, entrada(s) relacionadas, tipo de mensaje, secuencia,
intento, job, ID Meta cuando se devuelve y SHA. `event_key` deduplica logs y los
eventos estructurados incluyen `event_id`. INFO sale por stdout. Las métricas usan
relaciones explícitas y distinguen bot, seguimiento, humano, alerta interna y sistema.
Los registros históricos sin IDs quedan sin atribución; no se inventan respuestas
por proximidad. Una respuesta puede atender varias entradas consecutivas.

## Fuente comercial y discrepancias pendientes

La fuente comercial aprobada de Memo's sigue siendo su `system_instruction.txt`:
umbral mayorista $400.000; domicilio gratis Cali desde $100.000 y regional desde
$400.000; Jamundí martes/viernes, Palmira/Cerrito/Buga/Amaime martes y Yumbo miércoles.
No se trasladan cifras o rutas desde conversaciones antiguas al backend. La auditoría
reportó versiones distintas, pero no entregó valores alternativos aprobados para
sustituirlos. El titular comercial debe validar esas discrepancias y la presentación
de cinco libras antes de modificar la fuente. Este cambio no altera precios,
personalidad, catálogos ni reglas comerciales de Memo's.

## Preparación y despliegue posterior a la revisión

1. Revisar esta única solicitud hacia `42-actualizacion-de-precios` sin mergear
   todavía. Comprobar si Railway despliega automáticamente al hacer merge: en ese
   caso, preparar primero el SQL y detener los consumidores antiguos antes del merge.
2. En cada Supabase aislado existente, aplicar `supabase/upgrade_existing_brand.sql`.
   Para un proyecto nuevo usar únicamente `supabase/bootstrap.sql`. Verificar los
   nuevos campos de preferencias, metadata/event_key e `handoff_deliveries`.
   El upgrade es aditivo e idempotente y conserva las identidades opacas y datos.
3. Revisar `BUSINESS_ID`, número Meta, inbox, cola y credenciales propios sin copiarlos
   de otra marca. Confirmar el calendario del negocio. Opcionales: `BUSINESS_POLICY_JSON`,
   `FOLLOW_UP_MIN_SPACING_SECONDS` (default 900) y `FOLLOW_UPS_ENABLED` (default true).
4. Detener consumidores antiguos, incluido cualquier worker embebido en el web,
   antes de reanudar con este código. Con SQL listo, mergear y anotar el SHA
   resultante; no asumir que coincide con el head anterior al merge. Desplegar web
   y worker con ese mismo SHA.
   `RUN_WORKER_IN_WEB=false` puede iniciar un respaldo si no detecta un externo:
   no basta con escalar solo el worker a cero para detener consumidores.
5. Verificar `/health`: `commit` esperado, `workers_seen >= 1`, y todos los valores de
   `queue.worker_commits` iguales al SHA del web. Confirmar también `[WORKER] ... commit=`
   en los logs del servicio worker, sin workers antiguos activos.
6. Probar con contactos sintéticos (un número y un perfil con BSUID): entrada nocturna, apertura, dos trabajos
   acumulados, respuesta antes del envío, domingo, acuerdo para lunes y una solicitud
   B2B. Verificar un solo caso real, pausa activa y cero seguimientos durante atención.
7. Observar `FOLLOW_UP_SENT`, `FOLLOW_UP_REPROGRAMMED`, `FOLLOW_UP_SKIPPED`,
   `HANDOFF_CREATED/PENDING/FAILED` e `INTERNAL_ALERT_*`. Comparar tiempos por contacto:
   ninguna ráfaga ni separación menor a la configurada. Revisar casos `unknown` y
   `exhausted` de derivaciones en el endpoint autenticado; reconciliar Chatwoot antes de autorizar
   manualmente otro intento ambiguo. No se promete una verificación de producción
   hasta realizar estos pasos en el despliegue correspondiente.
   Para seguimientos ambiguos, buscar `provider_result_ambiguous_manual_review` en
   los eventos correlacionados y comprobar el proveedor antes de cualquier reenvío.

## Tratamiento de trabajos pendientes y reversión

No ejecutar FLUSHDB/FLUSHALL ni vaciar colas. Las firmas posicionales antiguas siguen
cargando. Los tokens históricos se adoptan con tombstone para no resucitarlos tras
cancelación. Claims antiguos cuyo envío no pueda distinguirse se omiten como
`legacy_claim_ambiguous`, sin bloquear una secuencia nueva. Un job sin ventana
original se omite como `legacy_missing_original_window`; si trae expiry pero carece
de timestamp original verificable en la entrada, se omite como
`legacy_missing_verified_original_timestamp`. Si existe esa evidencia, se usa el
menor vencimiento entre ella y el job; nunca recibe otras 24 horas.
Los mensajes obsoletos de panadería/postres se normalizan al ejecutar. Trabajos
acumulados válidos se reprograman respetando separación y vencimiento original.

Para una contención reversible, configurar `FOLLOW_UPS_ENABLED=false` en web y worker
de la marca afectada. El bot y la atención humana siguen funcionando; no se crean ni
envían recordatorios y los ya consumidos se registran como omitidos. Mantener SQL y
claves v2; reactivar solo después de verificar el mismo SHA y los indicadores.

Una reversión completa a código anterior elimina estas protecciones: detener primero
web y todos los workers, conservar los datos/Redis y no reanudar consumidores antiguos
con backlog sin revisar. Preferir corregir hacia adelante o revertir únicamente la
configuración comercial con los guards actuales activos. No eliminar columnas/tablas
nuevas ni resolver resultados ambiguos borrando sus claims.

## Validación local reproducible

Usar Python 3.12, un venv e instalar `requirements-dev.txt`. Ejecutar `python -m pytest -q`.
Las pruebas usan datos sintéticos, Gemini/Meta/Chatwoot simulados y fakeredis con Lua.
Para migraciones reales definir `POSTGRES_BIN_DIR` con initdb/pg_ctl/psql y ejecutar
como usuario sin privilegios; el fixture levanta una base temporal por Unix socket
y la elimina al terminar. Sin esos binarios, las pruebas SQL se marcan skipped.
No introducir secretos reales en la ejecución de pruebas.

Los cinco fallos que ya estaban en la referencia auditada eran expectativas antiguas
de texto/fixture, no fallos de instalación. Se actualizaron a las reglas aprobadas y
al BUSINESS_ID explícito del caso probado sin añadir contenido comercial nuevo.
