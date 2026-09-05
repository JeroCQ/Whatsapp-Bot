# Prompt para Lovable: administrador de múltiples catálogos

Copia y pega este prompt completo en el proyecto Lovable del dashboard:

> Reestructura la sección **Catalog Manager** para que cada perfil de negocio pueda administrar cero, uno o varios catálogos, sin asumir un catálogo universal. Mantén el `client_name` que el servidor ya inyecta y nunca permitas escoger ni enviar otro negocio.
>
> Muestra una tarjeta por catálogo y un botón visible **+ Agregar catálogo**. Al crear solicita: (1) **Nombre público**, el nombre bonito que verá el cliente en WhatsApp, obligatorio; (2) **ID de backend**, obligatorio solo técnicamente, autogenerado una única vez como `catalogo_<slug_en_minusculas>` y presentado bajo “Opciones avanzadas”; (3) descripción breve para que la IA sepa cuándo enviarlo; y (4) PDF/JPG/PNG/WEBP. Explica que el ID queda estable después de crear porque el system instruction lo referencia, mientras el nombre público sí se puede editar.
>
> En cada tarjeta deja únicamente estas acciones: **Editar nombre/descripción**, **Reemplazar archivo** y **Eliminar** con confirmación. No mezcles renombrar con reemplazar. Después de cada mutación vuelve a consultar el servidor y muestra nombre público, ID técnico, tipo, tamaño y última actualización. Presenta errores del backend sin ocultarlos y deshabilita doble submit.
>
> Usa exclusivamente las rutas server-side autenticadas existentes: `GET /api/catalogs?client_name=...`, `POST /api/catalogs?client_name=...` con JSON `{catalog_id, public_name, description}`, `PATCH /api/catalogs/{catalog_id}?client_name=...` con el mismo JSON, `POST /api/catalogs/{catalog_id}/file?client_name=...` multipart campo `file`, `GET /api/catalogs/{catalog_id}/file` para abrir el archivo activo y `DELETE /api/catalogs/{catalog_id}?client_name=...`. No llames Railway ni Supabase desde el navegador; conserva el proxy Lovable que agrega `X-Dashboard-API-Key` del lado servidor. No almacenes service-role ni dashboard key en variables `VITE_*`/cliente. El GET del archivo queda vinculado al `BUSINESS_ID` del deployment y no debe confiar en un `client_name` enviado por el navegador.
>
> Añade una vista de solo lectura **Así ve la IA los archivos disponibles**. Cárgala desde `GET /api/catalog-prompt-preview?client_name=...` y muestra literalmente el campo `prompt`, con una acción para actualizar. No reconstruyas el texto en el frontend: esta ruta comparte las reglas del runtime y excluye automáticamente las fichas que todavía no tienen archivo.
>
> La eliminación debe advertir: “Elimina el archivo y deja inválida cualquier referencia a este ID en el system instruction”. No permitas eliminar mientras se guarda o reemplaza.
>
> Añade validación y pruebas de UI para perfiles con un catálogo legado, tres catálogos y ninguno; aislamiento de dos `client_name`; creación, edición pública, reemplazo, eliminación, error 409 por ID duplicado y archivos inválidos/muy grandes. No cambies el editor de system instruction, pero muestra junto al ID un botón para copiarlo y el texto “Usa este ID exacto en requested_files”.
>
> Para Velvet crea, en este orden, `catalogo_tortas` / “Catálogo Tortas”, `catalogo_mochis` / “Catálogo Mochis” y `catalogo_mochis_mayorista` / “Catálogo Mochis Al Por Mayor”. Las descripciones deben indicar respectivamente tortas/postres, mochis al detal y cantidades desde 40 unidades/venta mayorista.

## Contrato y correlación

El nombre técnico estable es el contrato entre `requested_files` del system instruction y el backend. `catalog_assets` guarda ese ID junto al nombre público; el runtime consulta únicamente las filas de su `BUSINESS_ID`, agrega los IDs al prompt de Gemini y resuelve cada archivo en `catalogos/<BUSINESS_ID>/<catalog_id>.<ext>`. El nombre público se usa como caption/nombre comercial de WhatsApp y puede editarse sin cambiar el system instruction.

## Operación diaria desde Lovable (Tanaka y cualquier marca)

Una vez desplegado el backend y configurado el proxy, el operador no necesita Railway,
Supabase ni GitHub para agregar o reemplazar catálogos:

1. Inicia sesión en Lovable con la contraseña de la marca. La contraseña, y nunca un
   campo editable del navegador, selecciona el Railway, la API key y `client_name`.
2. Pulsa **Agregar catálogo** y escribe un nombre público claro.
3. Escribe una descripción operacional: qué contiene, ante qué solicitud debe enviarse
   y, si aplica, cuándo **no** debe enviarse. Por ejemplo: “Precios de mochis al detal;
   enviar cuando pregunten sabores o precios unitarios; no usar para pedidos de 40 o más”.
4. Revisa el ID generado `catalogo_<slug>`. Es permanente: no se renombra y no debe
   reutilizarse para otro propósito.
5. Adjunta PDF, JPG, PNG o WebP (hasta `DASHBOARD_MAX_CATALOG_MB`) y confirma. Lovable
   crea primero los metadatos y después carga el archivo; si la segunda operación falla,
   la tarjeta queda visible sin archivo para poder reintentarla.
6. Comprueba en la tarjeta el nombre, ID, tipo, tamaño y fecha. Para actualizar contenido,
   usa **Reemplazar archivo** sobre esa misma tarjeta: conserva el ID y la regla de uso.

La descripción no se copia permanentemente al archivo
`src/clients/<BUSINESS_ID>/system_instruction.txt`. En cada turno, el runtime consulta
`catalog_assets` para **ese** `BUSINESS_ID` y añade al system prompt efectivo una sección
“ARCHIVOS PREGUARDADOS DISPONIBLES” con el ID, tipo y descripción actuales. Gemini analiza
esa descripción junto con la conversación y solo puede devolver IDs existentes en
`requested_files`. Por eso editar la descripción en Lovable cambia la selección desde el
siguiente mensaje sin editar GitHub ni volver a desplegar. El editor de system instruction
sigue reservado para reglas generales o relaciones más complejas entre catálogos.

Lovable puede presentar el texto efectivo sin intentar recrearlo: `GET
/api/catalog-prompt-preview?client_name=<marca>` devuelve `client_name`, `prompt` y
`catalog_ids`. La previsualización y el bot comparten el mismo compositor; los catálogos
creados sin archivo siguen apareciendo como tarjetas administrativas, pero no aparecen en
el prompt ni pueden ser seleccionados por Gemini hasta completar la carga.

## Puesta en marcha

* **Proyecto nuevo:** ejecuta `supabase/bootstrap.sql` antes de conectar Railway.
* **Marca existente (incluido Tanaka):** ejecuta una vez
  `supabase/upgrade_existing_brand.sql`; agrega la tabla/columnas de catálogos sin borrar
  filas existentes.
* Configura por marca en el proxy `PASSWORD_<MARCA>`,
  `<MARCA>_DASHBOARD_BACKEND_URL` y `<MARCA>_DASHBOARD_API_KEY`. Cada URL debe apuntar al
  Railway aislado de esa misma marca y cada key debe coincidir con su `DASHBOARD_API_KEY`.
* Despliega el backend y el proxy. Después de esto, todas las altas y sustituciones se
  hacen exclusivamente en Lovable siguiendo los seis pasos anteriores.
