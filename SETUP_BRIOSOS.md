# Alta aislada de Briosos, sin handoffs

Este checklist aplica al **proyecto nuevo de Briosos**. Supabase ya recibió
`supabase/bootstrap.sql`; no hace falta ejecutar otro SQL para esta configuración.
No vuelvas a ejecutar el bootstrap sobre una base que ya tenga datos. Los
despliegues existentes conservan sus datos y siguen el
[runbook de actualización](docs/UPGRADE_EXISTING_BRANDS.md) cuando corresponda.

La [instrucción provisional](src/clients/briosos/system_instruction.txt) no supone
productos, precios, horarios, medios de pago ni cobertura. Luego se podrá editar
desde el panel actual de Lovable. El número Meta y el webhook ya están configurados,
según lo confirmado por el operador; falta comprobar su destino y el procesamiento
completo en el despliegue nuevo.

## 1. Confirmar qué servicio recibe el tráfico

Anota proyecto, entorno, nombre/ID del servicio web y dominio público. El nombre
del servicio y su dominio Railway son independientes: renombrar el servicio no
renombra automáticamente una URL generada que contiene `velvet`. Ese texto no
demuestra que se esté usando el bot anterior. Comprueba en Networking a qué
servicio pertenece el dominio y consulta su `/health`: debe devolver
`business_id=briosos`.

Conserva un dominio funcional mientras confirmas el destino. Si decides cambiarlo,
actualiza conjuntamente el callback Meta `https://<dominio-briosos>/webhook` y el
backend de Briosos en Lovable. No cambies el dominio ni el callback de Velvet.

## 2. Publicar y seleccionar el código

El servicio necesita un commit que incluya la carpeta `src/clients/briosos/`, el
flag efectivo `CHATWOOT_ENABLED` y `scripts/check_business_setup.py`.
**Poner las variables Chatwoot a `0` no desactiva la integración en código
anterior.** Esa configuración puede impedir el arranque.

Para una prueba aislada, selecciona explícitamente la rama publicada
`codex/briosos-bot-only-setup` **solo en el servicio nuevo de Briosos**. Tras
integrarla en la rama de producción, podrás apuntar ese servicio a la rama
definitiva. Comprueba siempre rama y SHA reales del deployment; no cambies los
enlaces GitHub de las otras marcas ni uses Deploy All.

Durante ese piloto, fija `GITHUB_BRANCH=codex/briosos-bot-only-setup` en el web
nuevo para que las ediciones de Lovable se guarden en la misma rama. Al pasar a
la rama definitiva, actualiza esa variable junto con la rama del servicio.

Conserva `railway.json` y el start command canónico. El launcher ya inicia el worker
RQ embebido: deja `RUN_WORKER_IN_WEB` sin definir y no crees otro worker para este
alta.

## 3. Revisar variables heredadas y Redis

Configura estos valores públicos en el web de Briosos:

```dotenv
BUSINESS_ID=briosos
QUEUE_NAME=whatsapp-events-briosos
CHATWOOT_ENABLED=false
FOLLOW_UPS_ENABLED=false
CATALOG_STORAGE_BUCKET=catalogos
PRESAVED_FILES_JSON=[]
GITHUB_SI_PATH=src/clients/briosos/system_instruction.txt
```

Elimina del servicio nuevo las variables Chatwoot de relleno y cualquier
credencial copiada de Velvet. Conserva `CHATWOOT_ENABLED=false`; con el código
nuevo el bot responde sin crear tickets, pausar por handoff ni aceptar comandos
de asesores. Las pausas operativas que no estén vinculadas a un handoff se respetan.
No se modifica el servicio Chatwoot compartido.

Retira un `BUSINESS_POLICY_JSON` heredado de Velvet para usar la política de
Briosos y un `GEMINI_OUTAGE_RECOVERY_SINCE` copiado de una reparación anterior.
Revisa también aliases de catálogo, `GITHUB_SI_PATH` y cualquier `GITHUB_BRANCH`
explícita. Los valores de GitHub deben corresponder a la rama elegida para Briosos;
el panel no debe guardar cambios en la instrucción de Velvet.

La política inicial contiene un calendario técnico genérico; **no confirma los
horarios comerciales de Briosos**. Mantén los seguimientos desactivados hasta
definir sus reglas y calendario. Los catálogos empiezan vacíos; después se podrán
subir y administrar dinámicamente desde Lovable, sin agregar una variable por
archivo ni reutilizar archivos de otro negocio.

Confirma, sin imprimir sus valores, que estén presentes:

| Variable | Procedencia |
| --- | --- |
| `SUPABASE_URL` | Raíz del proyecto propio, sin `/rest/v1`. |
| `SUPABASE_SERVICE_ROLE_KEY` | Clave secreta de servidor del Supabase propio; nunca anon/publishable. |
| `WA_PHONE_NUMBER_ID`, `WA_TOKEN`, `WA_VERIFY_TOKEN` | Número, credencial y verify token propios de Briosos. |
| `GEMINI_API_KEY` | Proyecto/cuota autorizados para Briosos. |
| `REDIS_URL` | Add Reference al Redis exclusivo del proyecto y entorno nuevos. |
| `DASHBOARD_API_KEY` | Secreto propio de Briosos, igual en el proxy de Lovable. |
| `GITHUB_TOKEN` | Acceso autorizado para editar la instrucción de esta rama. |

El fallo recurrente de las plantillas es una referencia `REDIS_URL` huérfana o
copiada, o un Redis sin deployment sano. Es una posibilidad documentada, **no un
diagnóstico confirmado de este proyecto sin sus logs**. Conserva un Redis sano;
si hay que crear uno, usa el oficial, exclusivo, con su start predeterminado, un
solo volumen y sin RedisBloom/plugins. Usa Add Reference contra su nombre vigente;
no pegues la URL resuelta de Velvet. Despliega Redis primero y el web después.
No borres datos, colas, jobs ni volúmenes para probar una reparación.

El [prompt listo para Railway Agent](docs/BRIOSOS_RAILWAY_AGENT_PROMPT.md) limita
el diagnóstico y las correcciones a este proyecto. Si el Agent no puede confirmar
el aislamiento o necesita una acción destructiva, debe informar el bloqueo.

## 4. Preflight y health

Dentro del entorno del servicio nuevo, con sus variables ya cargadas, ejecuta:

```bash
python scripts/check_business_setup.py
```

El preflight es offline, no modifica datos ni revela secretos. Revisa variables,
cola, instrucción y política; **no verifica** permisos, conectividad, propiedad de
recursos ni entrega de mensajes. Corrige los `failed_checks` antes de continuar.

Tras desplegar, `GET /health` debe mostrar:

* `business_id: "briosos"` y `chatwoot_enabled: false`;
* `queue_enabled: true` y `queue.enabled: true`;
* `queue.queue: "whatsapp-events-briosos"`, sin `queue.error`;
* `queue.workers_seen >= 1` y cada valor de `queue.worker_commits` igual al `commit`
  real del web, sin `unknown`.

Un HTTP 200 o `status: "ok"` no basta. Revisa también el deployment de Redis,
el destino de Add Reference y el log de inicio del worker embebido: el health no
demuestra por sí solo que los recursos sean exclusivos.

## 5. Añadir Briosos al panel actual de Lovable

En el proxy **server-side** del panel, añade la entrada `briosos` y su backend
propio. Sigue el convenio del panel para secretos; por ejemplo,
`BRIOSOS_DASHBOARD_BACKEND_URL`, `BRIOSOS_DASHBOARD_API_KEY` y
`BRIOSOS_DASHBOARD_PASSWORD`. Son configuración del proxy, no nombres que el bot
exija en Railway. El proxy fija `client_name=briosos` y envía
`X-Dashboard-API-Key` con el secreto que el backend guarda como
`DASHBOARD_API_KEY`.

No uses prefijo `VITE_` ni expongas claves de Railway, GitHub o Supabase al
navegador. No redirijas la entrada de Velvet al nuevo backend. Comprueba lectura
de instrucción e historial y, cuando haya un archivo autorizado, carga/listado de
catálogos. Una petición al backend Briosos con otra marca debe recibir HTTP 422.

Configura la rama de escritura GitHub para que coincida con el piloto elegido.
Guardar una instrucción crea un commit: si varios servicios observan esa misma
rama, Railway puede disparar sus autodeploys. Usa una rama aislada durante el alta
si quieres evitar ese efecto en servicios ya operativos.

El frontend de Lovable vive fuera de este repositorio; este cambio prepara el
backend, no modifica el panel. Con el dominio verificado, usa allí este prompt:

```text
Añade briosos al registro/selector de negocios del panel actual siguiendo su
arquitectura existente, con permisos y sesión propios. Conserva las rutas,
credenciales y accesos de los negocios ya operativos. No agregues una rama de
código especial por negocio si el registro existente puede expresar la entrada.

Su backend será <DOMINIO_BRIOSOS_VERIFICADO>. Antes de conectarlo, comprueba que
GET /health devuelve business_id=briosos y chatwoot_enabled=false. Configura URL,
API key y contraseña de Briosos solo en servidor, siguiendo el convenio actual
de secretos. No los imprimas, no uses VITE_* ni expongas claves al navegador.
Si falta un secreto, indica su nombre para cargarlo por la interfaz segura.

El proxy debe fijar client_name=briosos en servidor y enviar X-Dashboard-API-Key
al backend propio; no permita que el navegador elija otra marca. Integra lectura,
edición, guardado e historial del system instruction y el gestor dinámico de
catálogos con las mismas funciones de los otros negocios. Los archivos nuevos
deben aparecer desde la API, sin listas de IDs codificadas en el frontend.

Verifica acceso autorizado a Briosos y rechazo de cruces de marca, sin enviar
mensajes WhatsApp ni modificar datos o configuración de las otras marcas.
Entrega los cambios y las verificaciones realizadas, con los pendientes claros.
```

## 6. Prueba del operador y datos pendientes

Con el número de prueba que el operador elija, verifica recepción Meta, enqueue
en la cola de Briosos, consumo por el worker, respuesta Gemini, entrega Meta y
persistencia únicamente en su Supabase. `POST /webhook 200` confirma el ACK, no
todo ese recorrido. Pide un asesor: el bot debe explicar la atención automática
actual, continuar disponible y no prometer una transferencia. Prueba también un
audio y un catálogo después de subirlo. No se envían mensajes a clientes como
parte del preflight o de la revisión de infraestructura.

Para cerrar el alta faltan los nombres/IDs del proyecto, entorno y servicio web,
el dominio que realmente pertenece a Briosos y la configuración de su entrada
server-side en Lovable. No hace falta compartir secretos por chat. La información
comercial se podrá añadir después al system instruction.

## Habilitar asesores más adelante

Cuando se decida usar handoffs, crea/configura el account y API inbox propios,
completa las credenciales y firmas indicadas en el
[alta canónica](docs/NEW_BUSINESS_SETUP.md#4-chatwoot-opcional) y establece
`CHATWOOT_ENABLED=true`. Elimina de la instrucción provisional la indicación de
no derivar y prueba asignación, respuesta pública, firma y resolución. No basta
con cambiar el flag si faltan esos recursos. Los archivos vigentes de Lovable
podrán enviarse con los [comandos comunes del asesor](docs/CHATWOOT_ADVISOR_FILES.md).
