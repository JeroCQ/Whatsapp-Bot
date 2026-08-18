# ⚡ Quick Start: Deploy Worker en 10 Minutos

## 🎯 TL;DR - Lo Esencial

Tu Worker es un **servicio asincrónico que procesa tareas desde Redis**. Necesita:
1. Redis para la cola
2. PostgreSQL/Supabase para persistencia
3. Variables de entorno con tokens/keys
4. Start command: `python -m workers.runner`

---

## 5️⃣ Pasos Rápidos

### ✅ Paso 1: Crear Servicio Worker (2 min)
```
1. Railway Dashboard → New Service → GitHub Repo
2. Seleccionar: JeroCQ/Whatsapp-Bot, rama "memo's-3.1"
3. Nombre: whatsapp-worker
4. Click Deploy
```

### ✅ Paso 2: Actualizar railway.json (1 min)
```bash
# Reemplazar contenido de railway.json con:
{
  "$schema": "https://railway.schema.json",
  "build": {"builder": "NIXPACKS"},
  "deploy": {
    "startCommand": "python -m workers.runner",
    "restartPolicyType": "ON_FAILURE",
    "restartPolicyMaxRetries": 5
  }
}
```

### ✅ Paso 3: Agregar Variables (3 min)
En Railway UI → whatsapp-worker → Variables:

```
REDIS_URL = ${{ Redis.REDIS_URL }}
SUPABASE_URL = <tu_url>
SUPABASE_KEY = <tu_key>
WA_VERIFY_TOKEN = <token>
WA_TOKEN = <token>
WA_PHONE_NUMBER_ID = <id>
GEMINI_API_KEY = <key>
QUEUE_NAME = whatsapp-events
QUEUE_JOB_TIMEOUT_SECONDS = 180
GEMINI_MAX_CONCURRENT = 8
```

### ✅ Paso 4: Deploy (1 min)
```
1. Guardar y hacer commit del railway.json
2. Railway automáticamente detecta y deploya
3. O click Deploy manualmente en UI
```

### ✅ Paso 5: Verificar (3 min)
```bash
# Debería ver en logs:
[WORKER] Starting RQ worker for queue=whatsapp-events

# Testear health:
curl https://whatsapp-bot.railway.app/health/queue
# Debería retornar JSON con stats
```

---

## 📦 Componentes

```
API (main.py)
  ↓ enqueue tarea
Redis Queue
  ↓ espera worker
Worker (workers/runner.py)
  ↓ procesa
Supabase/PostgreSQL
  ↓ guarda resultado
```

---

## 🔑 Variables Requeridas

| Variable | Ejemplo | Dónde obtener |
|----------|---------|---------------|
| REDIS_URL | redis://red:pass@host:6379 | Servicio Redis en Railway |
| SUPABASE_URL | https://xxx.supabase.co | Panel Supabase |
| SUPABASE_KEY | eyJ... | Panel Supabase |
| WA_VERIFY_TOKEN | abc123 | Configuración WhatsApp |
| WA_TOKEN | EAABGd... | Configuración WhatsApp |
| WA_PHONE_NUMBER_ID | 1234567890 | Configuración WhatsApp |
| GEMINI_API_KEY | AIzaSyD... | Google AI Studio |

---

## ✨ Eso es TODO

El worker está listo cuando ves:
- ✅ Logs: `[WORKER] Starting RQ worker...`
- ✅ Health: `/health/queue` retorna stats
- ✅ Workers online: > 0

---

## 🆘 Si Algo Falla

### Error: "REDIS_URL must be set"
```
→ Falta variable REDIS_URL
→ Solución: Agregar en Railway Variables
```

### Error: "Connection refused"
```
→ Redis offline o mala URL
→ Solución: Verificar Redis service está online
```

### Worker se queda en logs antiguo
```
→ Container viejo en caché
→ Solución: Ir a Deployments → Redeploy (force)
```

---

## 📚 Documentación Completa

- **RAILWAY_WORKER_SETUP.md** - Guía detallada paso a paso
- **WORKER_DEPLOYMENT_EXAMPLES.md** - 10 ejemplos prácticos
- **COPY_PASTE_CONFIG.md** - Configs listas para copiar

---

## 🚀 Próximo: Monitoreo

Una vez deployado, monitorea el health endpoint:

```bash
while true; do
  curl https://whatsapp-bot.railway.app/health/queue | jq .
  sleep 10
done
```

Debería mostrar:
```json
{
  "enabled": true,
  "queue": "whatsapp-events",
  "queued_jobs": 0,
  "started_jobs": 0,
  "failed_jobs": 0,
  "workers_seen": 1
}
```

---

**¡Listo! Tu worker está deployado y procesando tareas en segundo plano.** 🎉

Próximos pasos:
1. Verificar que tareas se encolan desde API
2. Monitorear `/health/queue` endpoint
3. Ajustar número de replicas si hay mucha carga
4. Revisar logs en tiempo real: `railway logs --follow`

