# Permisos de promociones de Tanaka y Velvet

El webhook firmado del bot guarda cada respuesta en el historial con el negocio, el remitente, el ID de la respuesta y la referencia del mensaje original. El sincronizador de CRM usa esa evidencia para actualizar `chatty_crm.promotion_permission_events` y `chatty_crm.promotion_permissions`. La primera tabla conserva los eventos y la segunda contiene el estado vigente por negocio, remitente y contacto.

El sincronizador anterior a esta actualización copiaba la evidencia al historial sin actualizar estas tablas. La recuperación usa el mismo código de validación para las respuestas históricas de Black Week y para las nuevas. Reconoce los botones de alta y baja en sus versiones originales y abreviadas. La conexión debe usar `chatty_crm` con las dos tablas de permisos ya instaladas. La recuperación no requiere cambiar la revisión 0006.

Al arrancar `crm-staging-sync`, se revisa el historial de Tanaka y Velvet por separado, en páginas y con el bloqueo habitual de cada negocio. Un evento se registra una vez por su identidad de proveedor. El estado vigente usa el orden de los eventos y prioriza una baja o rechazo en caso de empate. Si una recuperación falla, el registro `black_week_permission_replay_failed` conserva el negocio y la categoría del error. Después de corregir la causa, usa el recuperador existente con la conexión del sincronizador.

```sh
python -m crm_service.replay_black_week_permissions tanaka
python -m crm_service.replay_black_week_permissions velvet
```

Comprueba cantidad y estado de los eventos validados en Supabase con la siguiente consulta. El historial y el estado vigente son datos distintos. Una respuesta posterior puede cambiar el estado vigente y ambos eventos conservan su lugar en el historial.

```sql
SELECT business_id, status, count(*)
FROM chatty_crm.promotion_permission_events
WHERE validated AND version = 'black-week-2026-v1'
  AND (business_id, sender_id) IN (
    ('tanaka', '1279018015292550'), ('velvet', '1330184450175963')
  )
GROUP BY business_id, status;
```

El estado proyectado permite filtrar en Supabase. Una campaña administrada en Meta Business Suite tiene su propio público. La operación de esa campaña debe aplicar las bajas comprobadas cuando prepare un público de envío.
