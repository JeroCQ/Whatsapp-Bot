# Prompt para Lovable: administrador de múltiples catálogos

Copia y pega este prompt completo en el proyecto Lovable del dashboard:

> Reestructura la sección **Catalog Manager** para que cada perfil de negocio pueda administrar cero, uno o varios catálogos, sin asumir un catálogo universal. Mantén el `client_name` que el servidor ya inyecta y nunca permitas escoger ni enviar otro negocio.
>
> Muestra una tarjeta por catálogo y un botón visible **+ Agregar catálogo**. Al crear solicita: (1) **Nombre público**, el nombre bonito que verá el cliente en WhatsApp, obligatorio; (2) **ID de backend**, obligatorio solo técnicamente, autogenerado una única vez como `catalogo_<slug_en_minusculas>` y presentado bajo “Opciones avanzadas”; (3) descripción breve para que la IA sepa cuándo enviarlo; y (4) PDF/JPG/PNG/WEBP. Explica que el ID queda estable después de crear porque el system instruction lo referencia, mientras el nombre público sí se puede editar.
>
> En cada tarjeta deja únicamente estas acciones: **Editar nombre/descripción**, **Reemplazar archivo** y **Eliminar** con confirmación. No mezcles renombrar con reemplazar. Después de cada mutación vuelve a consultar el servidor y muestra nombre público, ID técnico, tipo, tamaño y última actualización. Presenta errores del backend sin ocultarlos y deshabilita doble submit.
>
> Usa exclusivamente las rutas server-side autenticadas existentes: `GET /api/catalogs?client_name=...`, `POST /api/catalogs?client_name=...` con JSON `{catalog_id, public_name, description}`, `PATCH /api/catalogs/{catalog_id}?client_name=...` con el mismo JSON, `POST /api/catalogs/{catalog_id}/file?client_name=...` multipart campo `file`, y `DELETE /api/catalogs/{catalog_id}?client_name=...`. No llames Railway ni Supabase desde el navegador; conserva el proxy Lovable que agrega `X-Dashboard-API-Key` del lado servidor. No almacenes service-role ni dashboard key en variables `VITE_*`/cliente.
>
> La eliminación debe advertir: “Elimina el archivo y deja inválida cualquier referencia a este ID en el system instruction”. No permitas eliminar mientras se guarda o reemplaza.
>
> Añade validación y pruebas de UI para perfiles con un catálogo legado, tres catálogos y ninguno; aislamiento de dos `client_name`; creación, edición pública, reemplazo, eliminación, error 409 por ID duplicado y archivos inválidos/muy grandes. No cambies el editor de system instruction, pero muestra junto al ID un botón para copiarlo y el texto “Usa este ID exacto en requested_files”.
>
> Para Velvet crea, en este orden, `catalogo_tortas` / “Catálogo Tortas”, `catalogo_mochis` / “Catálogo Mochis” y `catalogo_mochis_mayorista` / “Catálogo Mochis Al Por Mayor”. Las descripciones deben indicar respectivamente tortas/postres, mochis al detal y cantidades desde 40 unidades/venta mayorista.

## Contrato y correlación

El nombre técnico estable es el contrato entre `requested_files` del system instruction y el backend. `catalog_assets` guarda ese ID junto al nombre público; el runtime consulta únicamente las filas de su `BUSINESS_ID`, agrega los IDs al prompt de Gemini y resuelve cada archivo en `catalogos/<BUSINESS_ID>/<catalog_id>.<ext>`. El nombre público se usa como caption/nombre comercial de WhatsApp y puede editarse sin cambiar el system instruction.
