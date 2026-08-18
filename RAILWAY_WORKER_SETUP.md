# Guía Completa: Replicar el Worker de WhatsApp en Railway

## 📋 Resumen Ejecutivo

Este documento proporciona instrucciones paso a paso para replicar el **Whatsapp-Worker** en Railway, un servicio worker asincrónico que procesa tareas desde una cola Redis usando RQ (Redis Queue) y Python.

---

## 🏗️ Arquitectura del Worker

```
┌─────────────────────────────────────────────────────────────────┐
│                      WHATSAPP BOT PROJECT                        │
├─────────────────────────────────────────────────────────────────┤
│                                                                   │
│  ┌──────────────────┐         ┌──────────────────┐              │
│  │   API Service    │         │  Worker Service  │              │
│  │  (FastAPI)       │         │   (RQ Worker)    │              │
│  │  - HTTP Webhooks │         │  - Procesa Tareas│              │
│  │  - Enqueue Tasks │         │  - Paralelo      │              │
│  └────────┬─────────┘         └────────┬─────────┘              │
│           │                            │                        │
│           └────────────┬───────────────┘                        │
│                        │                                        │
│                   ┌────▼─────────┐                              │
│                   │  Redis Queue  │                              │
│                   │  (Cola RQ)    │                              │
│                   └────┬─────────┘                              │
│                        │                                        │
│                   ┌────▼──────────────┐                         │
│                   │  PostgreSQL/      │                         │
│                   │  Supabase         │                         │
│                   │  (Data Store)     │                         │
│                   └───────────────────┘                         │
│                                                                   │
└─────────────────────────────────────────────────────────────────┘
```

### Componentes:
- **API Service**: Recibe webhooks, encoloa tareas
- **Worker Service**: Consume tareas de Redis, procesa en paralelo
- **Redis Queue**: Intermediario de tareas asincrónicas
- **PostgreSQL/Supabase**: Persistencia de datos

---

## 🚀 Paso 1: Crear el Servicio Worker en Railway

### 1.1 Acceder a Railway Dashboard
```
https://railway.com/project/0246acfa-546d-4a02-9a44-4c4ff2c4c98c
```

### 1.2 Crear un Nuevo Servicio desde GitHub
1. Click en **"New"** o **"Add Service"**
2. Seleccionar **"GitHub Repo"**
3. Conectar repositorio: `JeroCQ/Whatsapp-Bot`
4. Seleccionar rama: `memo's-3.1`
5. Nombre del servicio: `whatsapp-worker`

### 1.3 Configuración Inicial en Railway
```
Service Name: whatsapp-worker
GitHub Repo:  JeroCQ/Whatsapp-Bot
Branch:       memo's-3.1
Root Dir:     . (raíz del repo)
```

---

## 🔧 Paso 2: Configurar Build y Deploy

### 2.1 Railway.json para el Worker
Actualizar `railway.json` con esta configuración MEJORADA:

```json
{
  "$schema": "https://railway.schema.json",
  "build": {
    "builder": "NIXPACKS"
  },
  "deploy": {
    "startCommand": "python -m workers.runner",
    "restartPolicyType": "ON_FAILURE",
    "restartPolicyMaxRetries": 5,
    "healthcheckPath": null,
    "drainingSeconds": 30
  }
}
```

**Nota**: El worker NO necesita healthcheck HTTP porque no expone endpoints. Solo ejecuta `python -m workers.runner`.

### 2.2 Procfile (Configuración de Heroku compatible)
```procfile
web: python -m py_compile main.py chatwoot_api.py config.py database.py queue_client.py processing_lock.py workers/runner.py run_railway.py && python run_railway.py
worker: python -m workers.runner
```

En Railway, seleccionar el comando apropiado:
- **API Service**: `web` (ejecuta FastAPI)
- **Worker Service**: `worker` (ejecuta RQ Worker)

---

## 📦 Paso 3: Configurar Variables de Entorno

### 3.1 Variables REQUERIDAS para el Worker

El worker necesita acceso a:

| Variable | Origen | Descripción |
|----------|--------|-------------|
| `REDIS_URL` | Servicio Redis | URL conexión a Redis Queue |
| `DATABASE_URL` | Supabase/PostgreSQL | URL conexión a BD (alternativo a SUPABASE_URL/SUPABASE_KEY) |
| `SUPABASE_URL` | Supabase | URL del proyecto Supabase |
| `SUPABASE_KEY` | Supabase | API Key de Supabase |
| `GEMINI_API_KEY` | Google AI Studio | API Key de Google Gemini |
| `WA_VERIFY_TOKEN` | WhatsApp Config | Token de verificación de webhooks |
| `WA_TOKEN` | WhatsApp Config | Token de acceso WhatsApp |
| `WA_PHONE_NUMBER_ID` | WhatsApp Config | ID del número de teléfono WhatsApp |
| `QUEUE_NAME` | Configuración | Nombre de la cola Redis (default: `whatsapp-events`) |
| `CHATWOOT_BASE_URL` | Chatwoot (opcional) | URL base de Chatwoot |
| `CHATWOOT_API_TOKEN` | Chatwoot (opcional) | Token API de Chatwoot |
| `CHATWOOT_ACCOUNT_ID` | Chatwoot (opcional) | ID de cuenta en Chatwoot |
| `CHATWOOT_INBOX_ID` | Chatwoot (opcional) | ID de inbox en Chatwoot |

### 3.2 Agregar Variables en Railway UI

En el panel del servicio `whatsapp-worker`:
1. Click en **"Variables"**
2. Agregar manualmente O conectar desde servicios existentes

#### Opción A: Referencias a Otros Servicios (RECOMENDADO)
```
REDIS_URL = ${{ Redis.REDIS_URL }}
DATABASE_URL = ${{ Postgres.DATABASE_URL }}
SUPABASE_URL = [pegar valor]
SUPABASE_KEY = [pegar valor]
GEMINI_API_KEY = [pegar valor secreto]
...
```

#### Opción B: Valores Hardcodeados (NO RECOMENDADO para secretos)
```
REDIS_URL = redis://red-user:password@host:port
DATABASE_URL = postgresql://user:password@host:5432/database
...
```

---

## 🔌 Paso 4: Conectar Servicios Dependientes

### 4.1 Redis Queue
**El worker REQUIERE Redis para funcionar**

Opciones:
1. **Usar Redis existente en el proyecto** (PREFERIDO)
   - Si ya existe `redis` service, Railway automáticamente proporciona `REDIS_URL`

2. **Crear Redis nuevo** (si no existe)
   ```bash
   # En Railway UI: "New Service" → "Redis"
   ```

Verificar conexión:
```bash
# En logs del worker, debe mostrar:
[WORKER] Starting RQ worker for queue=whatsapp-events commit=abc123
```

### 4.2 PostgreSQL/Supabase
**El worker REQUIERE BD para persistencia**

Opciones:
1. **Usar Supabase externo** (actual)
   - Variables: `SUPABASE_URL`, `SUPABASE_KEY`
   - ✅ Ya configurado en tu proyecto

2. **Usar PostgreSQL de Railway** (opcional)
   - Crear servicio PostgreSQL en Railway
   - Pasar `DATABASE_URL`

---

## 🎯 Paso 5: Configurar Start Command

En Railway Dashboard → Servicio `whatsapp-worker` → Deployments:

### Opción A: Via railway.json (RECOMENDADO)
```json
{
  "deploy": {
    "startCommand": "python -m workers.runner"
  }
}
```

### Opción B: Via Railway UI
Settings → Deploy → Start Command:
```
python -m workers.runner
```

---

## ⚙️ Paso 6: Ajustes de Rendimiento

### 6.1 Número de Replicas (Paralelo)
En el panel del worker:
```
Deploy → Instances: 1 o más
- 1 replica: procesa 1 tarea a la vez
- 2 replicas: procesa 2 tareas en paralelo
- N replicas: procesa N tareas en paralelo
```

Recomendación:
```
GEMINI_MAX_CONCURRENT = 8 (en config.py)
Replicas = 1-2 (según carga)
```

### 6.2 Timeouts y Reintentos
Variables a configurar:

```
QUEUE_JOB_TIMEOUT_SECONDS = 180 (3 minutos)
QUEUE_RESULT_TTL_SECONDS = 3600 (1 hora)
QUEUE_FAILURE_TTL_SECONDS = 86400 (24 horas)
PHONE_LOCK_TTL_SECONDS = 180 (3 minutos)
```

En railway.json:
```json
{
  "deploy": {
    "restartPolicyType": "ON_FAILURE",
    "restartPolicyMaxRetries": 5,
    "drainingSeconds": 30
  }
}
```

### 6.3 Limites de Recursos (opcional)
```
CPU: 0.5 vCPU - 1 vCPU
Memory: 512 MB - 1 GB
```

---

## 🔍 Paso 7: Verificar y Debuggear

### 7.1 Logs del Worker
En Railway Dashboard → Servicio `whatsapp-worker` → Logs:

```
# ✅ Worker iniciado correctamente:
[WORKER] Starting RQ worker for queue=whatsapp-events commit=abc123def
Worker: <Worker name=...>
started_work_horse ...

# ✅ Procesando tareas:
Received job <uuid>
Processed job <uuid>

# ❌ Error de conexión Redis:
RuntimeError: REDIS_URL must be set to run queue workers

# ❌ Error de conexión BD:
psycopg2.OperationalError: could not translate host name ...
```

### 7.2 Queue Statistics
El servicio `bot.py` expone un endpoint `/health/queue` que muestra stats:

```bash
curl https://whatsapp-bot.up.railway.app/health/queue
```

Respuesta esperada:
```json
{
  "enabled": true,
  "queue": "whatsapp-events",
  "web_queue_mode": "external_worker",
  "queued_jobs": 5,
  "started_jobs": 2,
  "failed_jobs": 0,
  "deferred_jobs": 0,
  "workers_seen": 1
}
```

### 7.3 Redis CLI
Conectar directamente a Redis para inspeccionar cola:

```bash
# Desde terminal local:
redis-cli -u $REDIS_URL

# En Redis:
keys whatsapp-events:* # Ver trabajos
llen whatsapp-events:queue # Cantidad en cola
hgetall whatsapp-events:job:<id> # Detalles de tarea
```

---

## 🚀 Paso 8: Deploy Inicial

### 8.1 Via Railway UI
1. Ir a `whatsapp-worker` → Deployments
2. Click **"Deploy"** o esperar a que se dispare automáticamente (si auto-deploy está activo)
3. Monitorear logs en tiempo real

### 8.2 Via Railway CLI
```bash
railway link 0246acfa-546d-4a02-9a44-4c4ff2c4c98c
railway service whatsapp-worker
railway deploy
railway logs --follow
```

### 8.3 Validar Deployment
```bash
# Cuando ves esto en logs = SUCCESS:
[WORKER] Starting RQ worker for queue=whatsapp-events commit=abc123
```

---

## 📊 Monitoreo Continuo

### Health Checks
```bash
# API Service (cada 30s):
curl https://whatsapp-bot.up.railway.app/health

# Queue Stats (manual):
curl https://whatsapp-bot.up.railway.app/health/queue
```

### Métricas Clave a Monitorear
1. **Redis Connection Status**: ✅ Working
2. **Workers Online**: Should be 1+
3. **Jobs Queued**: Debe bajar con tiempo
4. **Failed Jobs**: Debe investigarse y reintentarse
5. **Worker CPU/Memory**: Debe estar estable

### Alertas a Configurar
```
- Worker crashed (auto-restart por ON_FAILURE)
- REDIS_URL not configured
- Database connection failed
- Job queue backing up (queued_jobs > 100)
```

---

## 🔒 Configuración Segura de Secretos

### ❌ NO HACER
```
GEMINI_API_KEY = sk-1234567890abcdef (hardcoded en repo)
SUPABASE_KEY = eyJhbGciOiJIUzI1NiIs... (visible en código)
```

### ✅ HACER
```
1. Guardar en Railway Variables (secreto)
2. Referenciar como ${{ variable_name }}
3. Railway maneja encriptación automática
```

Código Python (seguro):
```python
gemini_key = os.getenv("GEMINI_API_KEY")  # Leído de environment variables seguro
```

---

## 🧪 Testing del Worker Localmente

### Antes de Deployar
```bash
# 1. Crear venv
python -m venv venv
source venv/bin/activate

# 2. Instalar deps
pip install -r requirements.txt

# 3. Establecer variables local
export REDIS_URL="redis://localhost:6379"
export SUPABASE_URL="https://..."
export SUPABASE_KEY="..."
export GEMINI_API_KEY="..."
# ... más variables ...

# 4. Iniciar Redis local (Docker)
docker run -d -p 6379:6379 redis:latest

# 5. Ejecutar worker
python -m workers.runner

# 6. En otra terminal, encolar tareas
python -c "
from queue_client import enqueue
from bot import handle_message
enqueue(handle_message, 'test_phone', 'Hola, ¿cómo estás?')
"
```

---

## 🐛 Troubleshooting

| Problema | Causa | Solución |
|----------|-------|----------|
| Worker no inicia | `REDIS_URL` no configurada | Verificar que Redis service existe y está online |
| `RuntimeError: REDIS_URL must be set` | Variable de entorno faltante | Agregar `REDIS_URL` en Railway Variables |
| Worker se queda en logs antiguo | Stale container | Ir a Deployments → Redeploy (force) |
| Jobs nunca se procesan | Worker offline o cola no iniciado | Ver `health/queue` endpoint |
| Memory leak en worker | Tareas muy grandes o sin limpieza | Revisar `RESULT_TTL` y `FAILURE_TTL` |
| Timeout en tareas largas | Timeout muy bajo | Aumentar `QUEUE_JOB_TIMEOUT_SECONDS` |

---

## 📚 Referencias

### Código del Worker
```
/workers/runner.py        # Punto de entrada del worker
/queue_client.py          # Cliente de cola RQ
/config.py                # Variables de configuración
/database.py              # Conexión a Supabase
/bot.py                   # Lógica de procesamiento de tareas
```

### Dependencias Clave
```
redis>=5.0              # Cliente de Redis
rq>=1.14                # Queue framework (RQ)
fastapi>=0.109.0        # API framework
supabase>=2.0           # Cliente de Supabase
google-genai>=0.5.0     # Google Gemini API
```

### Documentación Externa
- RQ Docs: https://python-rq.org/
- Redis Docs: https://redis.io/
- Railway Docs: https://docs.railway.app/

---

## ✅ Checklist Final

Antes de considerar el worker "READY FOR PRODUCTION":

- [ ] `whatsapp-worker` service creado en Railway
- [ ] `REDIS_URL` configurada y testeada
- [ ] `SUPABASE_URL` y `SUPABASE_KEY` configuradas
- [ ] `GEMINI_API_KEY` y otros secretos agregados
- [ ] `startCommand` = `python -m workers.runner`
- [ ] Deploy exitoso (logs muestran worker iniciado)
- [ ] `/health/queue` retorna stats válidos
- [ ] Al menos 1 worker online en stats
- [ ] API encolando tareas a Redis correctamente
- [ ] Worker procesando tareas de la cola
- [ ] Logs sin errores de conexión
- [ ] Reintentos automáticos funcionando (ON_FAILURE)
- [ ] Timeout y TTL configurados según necesidad

---

## 🎓 Próximos Pasos

1. **Crear el servicio worker** con esta guía
2. **Configurar variables** (ver tabla 3.1)
3. **Deploy inicial** y monitorear logs
4. **Validar con `/health/queue`** endpoint
5. **Probar encolando tarea** desde API
6. **Ajustar réplicas y timeouts** según carga
7. **Configurar alertas** en Railway/Slack

---

**Última actualización**: 2026-08-18
**Versión**: 1.0
**Repositorio**: JeroCQ/Whatsapp-Bot (rama: memo's-3.1)

