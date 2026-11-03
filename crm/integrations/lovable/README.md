# Parche archivado de integración Lovable del piloto

**Ya aplicado y subido al frontend main, con autorización del usuario:**
`6060d7828cc7ff58fb2b644911b847404a71302d`. No reaplicar sobre el main actual.
El piloto sigue deshabilitado hasta configurar sus servicios y secretos.
Las instrucciones de revisión siguientes documentan la preparación del artefacto.

Este directorio contiene código ejecutable y un parche unificado para
`JeroCQ/chatty-catalog-compass`, base exacta
`1cda4a8069d189834837e082b378ae05629a1f02`. Durante la preparación el frontend original permaneció sin modificaciones.
El usuario levantó después la restricción de solo lectura y autorizó aplicar y
subir la integración a main; esa operación ya está verificada remotamente. La copia bajo `files/` permite revisar cada
archivo completo. No añade dependencias ni modifica manifiestos o lockfiles.

## Comportamiento implementado

- Conserva las pantallas, contraseñas, catálogos, instrucciones y chats existentes
  de Tanaka, Memo's y Velvet. Con el flag omitido no aparece el enlace del piloto.
- Añade un panel de piloto en `/chats?crm_pilot=1`, con acceso personal separado de
  las contraseñas Lovable existentes. No depende de que Briosos esté en el enum
  estático de bots del panel anterior ni obliga a cambiar ese enum o sus cuentas.
- El servidor comprueba una credencial personal en **cada** petición. Su identidad
  procede de `CRM_PILOT_OPERATOR_ID`, no de un campo de navegador. El secreto debe
  pertenecer a una única persona de pruebas, nunca a una marca o equipo. Rotarlo
  revoca el acceso en la próxima petición. La credencial solo queda en memoria de
  la pestaña y se borra al cerrar sesión/recargar; no se guarda en localStorage.
- El proxy servidor firma JWT HS256 de 120 segundos con issuer, audience, profile,
  allowed_businesses, iat y exp. Ningún JWT CRM, clave del bot o credencial Supabase
  llega al navegador. Las contraseñas Lovable no se envían al CRM.
- Inbox con búsqueda y cursor, buckets calculados exclusivamente por el backend,
  detalle/historial paginado y leído por actor. La primera página consulta cada
  cinco segundos; tras cargar más páginas se usa Actualizar para volver al inicio.
- Consulta `/control` cada tres segundos y después de cada acción; compositor solo
  con pausa confirmada, propietario correcto y `can_send=true`. Botones Tomar
  control, Enviar respuesta y Cerrar y devolver a IA.
- Reserva un command_id por clic y conserva en memoria su cuerpo/versión originales.
  No repite POST tras timeout o 202. Consulta el mismo comando; diferencia aceptación,
  entrega y lectura. Doble clic queda bloqueado. Después de 40 consultas automáticas
  continúa disponible Consultar resultado. Un unknown no habilita otro envío.
- Proxy con origen HTTPS fijo configurado, rutas/métodos/cuerpos/query permitidos,
  negocio fijado en servidor, headers reconstruidos (sin Origin/Sec-Fetch-Site,
  cookies ni contraseña personal), sin redirects ni reintentos automáticos.

No añade un emisor Meta. Solo llama al contrato CRM existente. No implementa un
nuevo directorio empresarial, SSO, adjuntos humanos ni plantillas fuera de ventana.
Este acceso dedicado de una persona es suficiente para el piloto, no una identidad
compartida que se deba distribuir a empleados.

## Variables exclusivas del servidor frontend

Ver `frontend.env.example`. No usar prefijos VITE_ ni exponer estos valores en una
variable pública del hosting.

```dotenv
CRM_PILOT_ENABLED=false
CRM_PILOT_BUSINESS_ID=briosos
CRM_BACKEND_URL=https://<dominio-crm-api-del-piloto>
CRM_JWT_SECRET=<mismo-secreto-del-crm-api>
CRM_PILOT_OPERATOR_ID=<identificador-estable-de-la-persona-de-pruebas>
CRM_PILOT_OPERATOR_SECRET=<credencial-personal-nueva-aleatoria-minimo-32-caracteres>
```

Solo cuando bot, CRM y worker del piloto estén listos, configurar
`CRM_PILOT_ENABLED=true` en el frontend piloto. Los secretos personal y JWT deben
ser distintos. El proxy rechaza como actor el identificador del negocio y los
perfiles compartidos anteriores. También rechaza reutilizar una PASSWORD_* como
credencial personal. No cambia las contraseñas existentes.

## Archivos del parche

Nuevos:

- `src/components/CrmPilotPane.tsx`
- `src/lib/crm-pilot-contract.ts`
- `src/lib/crm-pilot.server.ts`
- `src/lib/crm-pilot.server.test.mjs`
- `src/routes/api/public/crm-pilot/$.ts`

Cambios mínimos existentes:

- `src/routes/chats.tsx`: selector de panel por query y enlace condicionado.
- `src/routeTree.gen.ts`: registro generado de la nueva ruta servidor.

No cambia acceso, CLIENTS, AppNav, editor de instrucciones, catálogos o proxy
DASHBOARD_API_KEY. La ruta del piloto no acepta destinos ni actores del navegador.

## Revisión y aplicación posterior autorizada

En un checkout del frontend que siga en la base indicada:

```bash
git apply --check /ruta/Whatsapp-Bot/crm/integrations/lovable/crm-pilot.patch
# Aplicar solo después de levantar expresamente la restricción de solo lectura:
git apply /ruta/Whatsapp-Bot/crm/integrations/lovable/crm-pilot.patch
node --experimental-strip-types src/lib/crm-pilot.server.test.mjs
bun install --frozen-lockfile
bun run build
bunx tsc --noEmit
```

No hacer force push ni reescribir historia de Lovable. No publicar automáticamente
sobre su rama conectada. Si cambió la base, revisar los conflictos y volver a
validar en vez de sobrescribir cambios. Los datos y casos reales del piloto se
activan únicamente según `docs/CRM_PILOT_HANDOFF.md` del repositorio del bot.

## Validación realizada

La revisión usa una copia de fuentes no Git en `/tmp/crm-lovable-pilot-review`.
El frontend original conserva status limpio. `git apply --check` pasó sin aplicar
el parche. Los 10 tests Node reales pasan desde el artefacto empaquetado:

```bash
node --experimental-strip-types crm/integrations/lovable/files/src/lib/crm-pilot.server.test.mjs
```

Comprueban identidad individual, rotación/revocación por configuración, JWT exacto
y firma, aislamiento, headers, allowlists, identificadores opacos, no reintento y
los requisitos del compositor. Fetch está simulado; no se contacta Meta, Railway,
Supabase ni fuentes productivas.

El build de producción y TypeScript de la copia pasaron. La comprobación del
bundle público no encontró referencias al secreto JWT, secreto del operador o
Node crypto. ESLint estricto de los cuatro archivos TS/TSX nuevos pasa sin avisos.
La validación final después del empaquetado queda registrada en VALIDATION.json.

La instalación congelada original encontró URLs del mirror Lovable bloqueadas
por la red del entorno. En la copia desechable se sustituyó temporalmente solo el
origen de esas URLs por registry.npmjs.org para descargar las mismas versiones con
los mismos SHA-512 del lock. Bun verificó integridad. Se restauró el lock original
byte por byte; no se envía cambio de dependencia/lock en el parche.

Aplicación y push completados. Pendiente: configurar su servidor/hosting y activar
únicamente Briosos cuando bot/API/worker estén listos;
comprobar en navegador el ciclo físico en Briosos. Los tests de gateway y build no
constituyen demostración de recepción en un dispositivo WhatsApp real.
