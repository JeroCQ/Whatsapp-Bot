# Prompt para Railway Agent: proyecto nuevo de Briosos

Este prompt se aplica únicamente al proyecto nuevo de Briosos. El problema
recurrente documentado en [el alta canónica](NEW_BUSINESS_SETUP.md#5-railway) es
una referencia Redis copiada o huérfana, un Redis sin deployment sano o un Redis
personalizado. Sin los logs de Briosos, ninguna de esas causas está confirmada.

Antes de pegarlo, publica la rama `codex/briosos-bot-only-setup`, que contiene la
instrucción de Briosos y el soporte de `CHATWOOT_ENABLED=false`. El prompt permite
seleccionar esa rama únicamente en el servicio nuevo de Briosos, para una puesta
en marcha aislada. Poner todas las variables Chatwoot a `0` no desactiva la
integración en versiones anteriores: puede impedir el arranque. El prompt no
autoriza commits/merges en GitHub ni el envío de mensajes de prueba.

```text
Configura exclusivamente el proyecto NUEVO de Briosos y su entorno seleccionado.
Primero identifica proyecto, entorno y servicios por sus IDs/nombres; confirma
que el web tiene BUSINESS_ID=briosos. Si hay ambigüedad entre proyectos, entornos
o servicios, pregunta cuál corresponde antes de cambiar nada. No toques Velvet,
Tanaka, Memo's, Chatwoot compartido ni recursos de otras marcas.

1. Inspecciona los deployments y logs actuales de Redis y del web y el grafo de
   referencias. Explica la causa observada; no atribuyas automáticamente el fallo
   a la plantilla. No muestres secretos, URLs de conexión ni mensajes de clientes.
   Identifica también el dominio público del web: renombrar un servicio no cambia
   automáticamente su dominio Railway. Que el texto de la URL conserve velvet
   no demuestra un error. Comprueba a qué servicio pertenece y si el callback
   /webhook configurado para el número propio de Briosos apunta a ese web. No
   renombres ni elimines un dominio ni cambies el webhook de Meta en esta revisión.

2. Para esta puesta en marcha aislada, selecciona la rama publicada
   codex/briosos-bot-only-setup del repositorio JeroCQ/Whatsapp-Bot exclusivamente
   en el web NUEVO de Briosos. No cambies el repositorio ni la fuente de ningún
   otro servicio. Comprueba que el commit incluye
   src/clients/briosos/system_instruction.txt y soporte efectivo de
   CHATWOOT_ENABLED=false. Si esa rama no existe o falta ese código, informa el
   bloqueo antes de desplegar: no supongas que una variable nueva cambia código
   antiguo. No crees commits, no hagas merge ni cambies configuración global de
   GitHub o despliegues de otras marcas. No uses Deploy All.

3. Briosos necesita Redis oficial y exclusivo dentro de este proyecto y entorno,
   con su start command predeterminado, un solo volumen y sin RedisBloom/plugins.
   Conserva un Redis existente si ya cumple estas condiciones y está sano.
   Si falta Redis, puedes agregar uno oficial nuevo y aislado solo a Briosos.
   Si el existente es compartido, tiene imagen/start personalizado, espera
   RedisBloom o está roto, diagnostica sin borrar ni recrear sus recursos. Si
   corregirlo requiere borrar datos/servicios/volúmenes, cambiar credenciales o
   afectar otro consumidor, detén esa reparación e informa qué acción falta.
   No uses FLUSHALL, FLUSHDB ni elimines colas o jobs.

4. En el web de Briosos crea/corrige REDIS_URL mediante Add Reference al REDIS_URL
   del servicio Redis vigente y exclusivo del mismo proyecto y entorno. Sustituye
   la referencia huérfana o la URL literal copiada; no reutilices la URL ni las
   credenciales Redis de Velvet. Comprueba el destino de la referencia sin revelar
   su valor. No inventes REDIS_HOST, REDIS_PORT ni variables RedisBloom.

5. Fija BUSINESS_ID=briosos, QUEUE_NAME=whatsapp-events-briosos y
   CHATWOOT_ENABLED=false en el web. Elimina los valores Chatwoot de relleno 0
   y las credenciales Chatwoot copiadas del web de Briosos; conserva el flag false.
   Esto no autoriza modificar servicios ni cuentas Chatwoot. Deja
   RUN_WORKER_IN_WEB sin definir para usar el worker embebido; no crees un worker
   separado. Deja FOLLOW_UPS_ENABLED=false mientras no estén confirmados el
   calendario y las reglas de seguimiento de Briosos.

6. Revisa presencia y procedencia de las variables de Supabase, Meta y Gemini
   sin imprimir sus valores. Supabase y Meta deben pertenecer a Briosos;
   SUPABASE_URL debe ser la raíz del proyecto, sin /rest/v1. El bootstrap de su
   Supabase ya fue ejecutado: no ejecutes SQL, migraciones ni resets. No cambies
   datos, números, tokens ni variables de otras marcas. Si falta un secreto o no
   puedes confirmar su procedencia, informa el nombre de la variable pendiente.
   Conserva el repositorio y token GitHub existentes. Para que Lovable guarde la
   instrucción de este piloto en la rama que ejecuta, fija GITHUB_BRANCH a
   codex/briosos-bot-only-setup y GITHUB_SI_PATH a
   src/clients/briosos/system_instruction.txt solamente en el web de Briosos.
   Para este alta inicial sin archivos fija PRESAVED_FILES_JSON=[] y
   CATALOG_STORAGE_BUCKET=catalogos. Retira BUSINESS_POLICY_JSON y
   GEMINI_OUTAGE_RECOVERY_SINCE solamente si son valores heredados de Velvet;
   no sustituyas una configuración propia de Briosos ya confirmada. El calendario
   técnico local de Briosos no es un horario comercial confirmado. No copies
   catálogos ni información comercial de Velvet.

7. Conserva railway.json, el start command canónico del web y su política de
   reinicio. Si el commit contiene scripts/check_business_setup.py, ejecútalo
   dentro del entorno del web: es un preflight offline y no prueba credenciales
   ni conectividad. No imprimas el entorno completo. Despliega primero Redis y
   espera a que esté sano; después despliega únicamente el web de Briosos.
   Si aparece Waiting for dependencies, revisa primero Redis y la referencia
   REDIS_URL; no cambies código, SQL ni el start command para resolverlo.

8. Verifica el log del worker embebido y GET /health con profundidad:
   business_id=briosos, chatwoot_enabled=false, Redis habilitado,
   cola whatsapp-events-briosos, ausencia de queue.error,
   workers_seen >= 1 y todos los worker_commits iguales al commit real del web,
   sin unknown. Un HTTP 200 o status=ok no bastan. Confirma también la marca en la
   configuración/logs; /health por sí solo no demuestra aislamiento del proyecto.
   Si estas comprobaciones fallan, informa el fallo; no declares el alta lista.
   No envíes WhatsApp, no generes mensajes de clientes y no actives campañas ni
   recordatorios. La prueba real de Meta/Gemini y persistencia queda para el
   operador, con su número de prueba y autorización de envío.

Entrega proyecto/entorno y servicios afectados, rama/commit comprobados, causa
encontrada, cambios realizados y checks aprobados o pendientes. Incluye solo
nombres de variables y resultados; nunca valores de secretos o mensajes.
```

El preflight offline se ejecuta con el entorno del servicio ya cargado:

```bash
python scripts/check_business_setup.py
```

No recibe secretos por argumentos ni carga archivos `.env`. Devuelve JSON con
nombres de checks y booleanos; sale con código `1` si faltan requisitos locales.
Un resultado correcto no comprueba permisos de proveedores, pertenencia de
recursos, SHA desplegado, workers activos ni entrega de mensajes.
