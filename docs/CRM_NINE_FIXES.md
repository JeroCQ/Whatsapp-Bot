# Aplicación y comprobación manual de los nueve ajustes

Código sobre `feat/crm-media-delivery@0c3464f` (bot/CRM) y `main@7a76cf0`
(frontend), publicado únicamente en `fix/crm-nine-focused-fixes` de cada repositorio.
No se ejecutaron pruebas, app, lint, TypeScript, builds, navegadores, Docker,
migraciones ni llamadas reales a proveedores. **Not executed or runtime-validated.**

## Qué conservar y aplicar

1. Incorporar por revisión el backend en `feat/crm-media-delivery` y el frontend
   en `main`. Actualizar manualmente el API y sync CRM existentes antes de publicar
   el frontend conectado a Lovable. Conservar roots, comandos de arranque y secretos.
   No se necesitan dependencias, infraestructura, variables ni migraciones CRM nuevas.
2. Web Push sigue requiriendo **en API y worker/sync CRM**
   `NOTIFICATIONS_ENABLED=true`, la misma `VAPID_PRIVATE_KEY` y un
   `VAPID_SUBJECT` válido (`mailto:` o HTTPS). Son requisitos del código,
   **no una confirmación de que falten en producción**. La clave pública se obtiene
   del API autenticado; nunca se añade al frontend una clave privada ni otro login.
3. `/reset` ya existe antes de Gemini en `main.py`, y el contexto de `bot.py`
   pasa por `database.get_message_logs`, que corta en el último marcador `crm_reset`.
   La función SQL limpia customer_data/order_summary y el token de seguimiento,
   conserva el historial y rechaza reset durante pausa humana o envío incierto.
   Un `unsupported_control_action` significa que el dispatcher ejecutado no admite
   la acción; el código actual sí la incluye. No prueba que Gemini fallara.
   Si ese error continúa con este código, revisar/aplicar el archivo exacto
   [`supabase/upgrade_crm_operator_transfer.sql`](../supabase/upgrade_crm_operator_transfer.sql)
   **en el Supabase operativo propio de cada bot afectado** (Tanaka, Memo’s,
   Velvet o Briosos), con respaldo y revisión. Nunca en PostgreSQL del CRM ni en
   el Supabase de otro negocio. El script conserva también transferencia y demás
   operaciones de control. No se ha consultado ni ejecutado SQL real.

## Definición del tiempo de espera

`classification.attention_since` parte del mensaje de cliente sin responder más
antiguo del ciclo posterior al último RESOLVED. Si no existe y se espera atención,
usa el handoff actual; sin él, last_interaction o el primer evento del ciclo.
`waiting_seconds` es ahora UTC menos ese instante, con mínimo cero. Puede preceder
al handoff: no significa que un asesor lleve ese tiempo asignado.

La UI muestra **Esperando asesor desde…** solo en detalle, con
`pending_handoff_at` de un handoff pendiente válido, no futuro. Sin ese dato lo
oculta. La lista solo muestra `last_activity_at` de customer/bot/advisor, en
America/Bogota: hoy `5 min`/`2 h`; días anteriores, fecha. No se reescriben fechas.

## Comprobaciones manuales pendientes (solo tu propio número WhatsApp de prueba)

1. **Último mensaje:** mensaje real en Humano e IA actualiza hora/orden; take,
   read, recibo y refresh no lo hacen. En móvil, nombre truncado y hora visible;
   comprobar fecha de ayer en Bogotá sin alterar datos.
2. **No leídos:** dos entradas dan dos no leídos a ese operador; abrir y mostrar
   los últimos mensajes con la app visible confirma el acuse y limpia el contador.
   Otra entrada lo restaura. Leer no cambia cola, propietario ni pausa. No marcar
   mensajes nuevos que lleguen mientras se leen páginas antiguas o con la app oculta.
3. **Navegación:** cuentas de negocio ven Chats → Gestión Bot → Notificaciones;
   Admin conserva sus entradas. Comprobar Tanaka/Velvet compartidos y Memo’s separado.
4. **Espera:** solo aparece en detalle con handoff pendiente válido; no hay métricas
   inventadas ni tiempos de espera en la lista.
5. **Sin Todo:** solo Humano/IA; enlaces antiguos `bucket=all`, Todo y closed abren
   Humano sin bucles, conservando chat seleccionado y todo el historial.
6. **Optimismo:** enviar texto muestra burbuja pendiente antes del acuse; al sincronizar
   queda una sola por command_id. Aceptado no equivale a entregado/leído. Ante timeout,
   consultar el mismo comando; no reenviar para comprobar.
7. **Avisos:** activar con clic desbloquea sonido local y solicita permiso Push cuando
   sea compatible. Un nuevo handoff, transferencia o entrada que requiera ese operador
   produce un aviso, sin repetirlo por refresh, paginación, dos pestañas o llegada Push.
   Recargar/navegar con el mismo operador conserva inscripción. Comprobar primer plano,
   segundo plano y app cerrada por separado. Comprobar destinataria Tanaka/Velvet y que
   Memo’s Detal/Mayoristas solo reciban su cola. Logout/cambio de operador elimina el
   vínculo anterior; ninguna pestaña antigua debe restaurarlo. Revisar los cuatro estados
   de Notificaciones, permisos denegados y suscripción caducada. En iPhone/iPad usar
   iOS 16.4+ y app añadida a pantalla de inicio, no iframe de Lovable. El sonido en
   segundo plano depende del navegador/SO; ni alta ni HTTP del proveedor prueban display.
8. **Reset:** con IA activa, aportar un pedido ficticio, `/reset` y comprobar nueva
   conversación sin esa memoria y sin nuevo seguimiento. Historial, archivos y auditoría
   deben conservarse. Bajo control humano, `/reset` debe registrar su motivo, conservar
   propietario/pausa y no enviar respuesta automática ni reactivar IA.
9. **Fotos:** elegir tres imágenes, quitar/reponer una, añadir un texto y enviar una vez.
   Recibir exactamente tres fotos, texto solo en la primera y una burbuja/command_id por
   imagen. Comprobar el límite diez y los límites individuales actuales. Con una respuesta
   incierta, consultar el mismo ID antes de continuar; fotos aceptadas no se repiten.
   Cancelar detiene únicamente las no intentadas. Cambiar de chat/cuenta o perder control
   no puede redirigir la siguiente imagen. Confirmar que audio, documento y video siguen
   siendo individuales y requieren pausa/propiedad/ventana confirmadas.
