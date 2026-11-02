# Archivos de Lovable enviados por el asesor desde Chatwoot

El asesor puede enviar el catálogo vigente sin descargarlo al teléfono ni volver a cargarlo
en Chatwoot. El mecanismo es el mismo para Tanaka, Memo's, Velvet y un nuevo `BUSINESS_ID`.
Cada deployment consulta únicamente su catálogo de Supabase y usa su número de Meta.

## Uso después de desplegar este cambio

1. Abre la conversación correcta del cliente en Chatwoot.
2. Cambia el editor de respuesta a **Nota privada**; comprueba el indicador de nota interna.
3. Escribe `/archivos` y envía esa nota.
4. Aparecerá una lista privada con los nombres y comandos exactos de los archivos actuales.
   Si hay muchos, la lista se divide en varias notas.
5. Copia el comando del archivo elegido y envíalo como una **nueva nota privada**; por ejemplo,
   `/catalogo catalogo_portafolio` solo si ese ID aparece en la lista de ese negocio.
6. Revisa la siguiente nota privada. Si Meta aceptó el envío, lo indicará. Esto no prueba que
   haya llegado al dispositivo del cliente. Si hay un error, lo indicará sin enviar texto
   técnico al cliente.

`/catalogo` sin ID también muestra la lista. Los comandos públicos anteriores siguen
funcionando, pero es preferible utilizar notas privadas. Los comandos mal escritos dentro
de estas dos familias generan instrucciones privadas y no se envían como texto a WhatsApp.
No adjuntes un archivo al comando.

## Catálogo dinámico

La lista se consulta otra vez al listar y al enviar. Un archivo añadido en Lovable aparece
sin tocar código, Railway ni respuestas predeterminadas. Un reemplazo conserva su ID y usa
el archivo vigente. Las fichas sin carga completada no se listan. Los IDs eliminados o de
otro negocio no se aceptan.

No se selecciona automáticamente un portafolio por similitud de nombre: cada negocio puede
tener varios archivos y el asesor elige uno de la lista. Si falla la consulta a Supabase,
el comando se detiene y avisa; no utiliza una copia en memoria por suposición.
El catálogo heredado explícitamente configurado en `PRESAVED_FILES_JSON` conserva su
compatibilidad cuando no hay filas administradas, igual que en el bot existente.

`GET /api/catalogs` devuelve también `chatwoot_command`, calculado a partir del ID estable,
o `null` para fichas pendientes. Lovable puede añadir un botón **Copiar comando para Chatwoot**
usando ese campo; no necesita registrar comandos por archivo. Ese botón requiere editar el
frontend de Lovable por separado. `/archivos` funciona sin modificarlo.

## Controles conservados

Solo un evento autenticado y del account/inbox de ese deployment llega al procesador.
Los controles privados requieren un mensaje saliente de un usuario de Chatwoot. Mensajes
entrantes de clientes y notas normales no ejecutan comandos. Las respuestas privadas del
bot no coinciden con la gramática de los comandos y no crean bucles.

Consultar la lista no pausa el bot, no cancela seguimientos y no envía nada a Meta. Para
enviar se exige una asociación conversación→cliente y una ventana original de WhatsApp de
24 horas verificable. El envío deja el caso con atención humana e invalida sus seguimientos.
El comando no amplía ni reinicia esa ventana.

Con el registro de eventos disponible, la reclamación del webhook existente evita repetir
el mismo evento. Conserva el comportamiento previo de continuar si falla ese registro;
no constituye una garantía absoluta de entrega única. Un error al publicar
la nota de confirmación o guardar un registro después de la aceptación de Meta no reenvía
el archivo. Si falla el envío, no repitas comandos a ciegas: revisa la causa y la conversación.
No se cambia el comportamiento del botón Reintentar propio de Chatwoot.

## Qué significa el aviso de 5 MB

5 MB no es un máximo universal para PDF en Chatwoot. En la versión usada, v4.16.2:

- El límite de instalación web predeterminado es 40 MB, configurable mediante
  `MAXIMUM_FILE_UPLOAD_SIZE` en la configuración de instalación.
- La interfaz aplica el menor entre el límite de instalación y el del canal/tipo.
  El canal API usado por esta integración toma por defecto 40 MB. WhatsApp nativo
  distingue imágenes de 5 MB y documentos de 100 MB; Twilio tiene otras restricciones.
- La app móvil tiene sus propios controles y puede diferir de la interfaz web. Cambiar
  solo el servidor no garantiza modificar su límite local.

La causa del aviso de 5 MB de la instalación concreta no se verificó con acceso a producción.
La captura recibida el 31 de octubre muestra un handoff por Gemini HTTP 504 y no contiene
el aviso de tamaño. No se modificó la configuración de Chatwoot para adivinar su causa.

El comando evita la carga desde la interfaz móvil/web y el bucket de Chatwoot: reutiliza
el archivo del bucket de catálogos de ese negocio y el transporte existente hacia Meta.
Siguen aplicando los límites de Lovable/Supabase configurados y los de Meta. Para PDF,
WhatsApp permite hasta 100 MB; no existe envío ilimitado. Este cambio no modifica límites
de tamaño ni formatos del transporte.

Fuentes oficiales verificadas:

- [Valor de instalación en v4.16.2](https://github.com/chatwoot/chatwoot/blob/70e284a044f00326725f65f703162745371075ec/config/installation_config.yml#L92).
- [Aplicación del límite en la interfaz](https://github.com/chatwoot/chatwoot/blob/70e284a044f00326725f65f703162745371075ec/app/javascript/dashboard/mixins/fileUploadMixin.js#L26).
- [Prioridad de InstallationConfig frente a variables](https://github.com/chatwoot/chatwoot/blob/70e284a044f00326725f65f703162745371075ec/lib/global_config_service.rb#L2).
- Reglas de canal en `@chatwoot/utils` 0.0.56, dependencia de esa versión:
  [paquete publicado](https://registry.npmjs.org/@chatwoot/utils/-/utils-0.0.56.tgz),
  `package/src/fileUploadRules.ts`.
- [Límite independiente del móvil, código consultado](https://github.com/chatwoot/chatwoot-mobile-app/blob/389f6de36f72b84b7425abadfa0666be2025bb91/src/constants/index.ts#L36).

## Instalación y validación

Aplica a proyectos nuevos y existentes que ya disponen del catálogo canónico. No añade
tablas, columnas, SQL, credenciales ni variables. Conserva los system instructions y
los datos comerciales. Revisa y mezcla el PR en la rama de despliegue habitual; espera
a que cada backend y su worker RQ carguen el mismo commit. No requiere desplegar Sidekiq
ni reiniciar las dependencias de Chatwoot.

En un chat de pruebas elegido por el operador: lista los archivos, envía un PDF mayor de
5 MB que ya esté en Lovable y verifica su recepción en WhatsApp. Reemplázalo desde Lovable
y verifica que el mismo comando usa el nuevo contenido. Comprueba que una ficha pendiente
no aparece y un ID eliminado no se envía. Las pruebas automáticas usan proveedores y
clientes sintéticos; no sustituyen esta verificación completa de producción.

Validación automatizada de esta entrega: 841 pruebas aprobadas y tres omitidas por falta
de binarios PostgreSQL. Persiste un fallo previo en
`test_memos_instruction_is_available_to_the_selected_deployment`, que exige un dato bancario
antiguo ya sustituido en las instrucciones aprobadas de Memo's. Este cambio no modifica
esas instrucciones ni restaura datos comerciales para hacer pasar el test.
