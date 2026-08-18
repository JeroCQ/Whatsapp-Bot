# Ejemplos Prácticos: Deployar el Worker

## 📝 Ejemplo 1: railway.json Optimizado para Worker

Actualiza el `railway.json` existente con esta configuración:

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
    "drainingSeconds": 30
  }
}
```

**Explicación**:
- `startCommand`: Inicia el RQ Worker
- `ON_FAILURE`: Reinicia automáticamente si falla
- `restartPolicyMaxRetries`: 5 intentos antes de darse por vencido
- `drainingSeconds`: Espera 30s para que tareas terminen antes de detener

---

## 🔧 Ejemplo 2: Variables de Entorno (Completas)

### Paso 1: Crear Redis (si no existe)
```bash
# En Railway Dashboard:
# New Service → Redis Template
# Railway automáticamente crea REDIS_URL
```

### Paso 2: Agregar Variables de Entorno

En Railway Dashboard → `whatsapp-worker` → Variables:

```env
# ===== REDIS Y COLA =====
REDIS_URL=${{ Redis.REDIS_URL }}
QUEUE_NAME=whatsapp-events
QUEUE_JOB_TIMEOUT_SECONDS=180
QUEUE_RESULT_TTL_SECONDS=3600
QUEUE_FAILURE_TTL_SECONDS=86400

# ===== BASE DE DATOS =====
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_KEY=eyJhbGciOiJIUzI1NiIs...

# ===== WHATSAPP CONFIG =====
WA_VERIFY_TOKEN=your-verify-token-123
WA_TOKEN=EAABGd...
WA_PHONE_NUMBER_ID=1234567890

# ===== GEMINI AI =====
GEMINI_API_KEY=AIzaSyD...
GEMINI_MAX_CONCURRENT=8

# ===== CHATWOOT (OPCIONAL) =====
CHATWOOT_BASE_URL=https://chatwoot.example.com
CHATWOOT_API_TOKEN=your-token
CHATWOOT_ACCOUNT_ID=1
CHATWOOT_INBOX_ID=1

# ===== LOCKS Y TIMEOUTS =====
PHONE_LOCK_TTL_SECONDS=180
PRESAVED_FILES_JSON=[]
catalogo_memos=[]
```

**Nota de Seguridad**: 
- Valores marcados con `${{...}}` se resuelven desde otros servicios
- Valores como `AIzaSyD...` deben ser valores reales, marcados como "secret" en Railway
- Nunca commitear valores reales en git

---

## 🚀 Ejemplo 3: Crear Servicio Worker via CLI

```bash
# 1. Conectar a proyecto Railway
railway link 0246acfa-546d-4a02-9a44-4c4ff2c4c98c

# 2. Crear nuevo servicio desde GitHub
railway service create whatsapp-worker \
  --github JeroCQ/Whatsapp-Bot \
  --branch "memo's-3.1"

# 3. Esperar a que se muestre en UI, luego:
railway service whatsapp-worker

# 4. Agregar variables (opción manual en UI, o JSON):
# → Ir a Dashboard, agregar variables manualmente

# 5. Deploy
railway deploy

# 6. Ver logs en tiempo real
railway logs --follow
```

---

## 📊 Ejemplo 4: Estructura de Logs Esperados

### ✅ Worker Iniciado Correctamente
```
Aug 18 17:45:23.123 INFO [WORKER] Starting RQ worker for queue=whatsapp-events commit=abc123def456
Aug 18 17:45:23.456 INFO Worker: <Worker name=railway-worker-1, default_result_ttl=3600>
Aug 18 17:45:23.789 INFO Worker is horse, starting work horse.
Aug 18 17:45:24.012 INFO started_work_horse 12345
```

### ✅ Procesando Tareas
```
Aug 18 17:46:00.111 INFO Received job 567e89ab-cdef-1234-5678-90abcdef1234
Aug 18 17:46:01.222 INFO Job 567e89ab completed
Aug 18 17:46:02.333 INFO Received job 234f56cd-efgh-5678-9012-34cdef567890
```

### ❌ Error: REDIS_URL No Configurada
```
Traceback (most recent call last):
  File ".../workers/runner.py", line 21, in main
    raise RuntimeError("REDIS_URL must be set to run queue workers")
RuntimeError: REDIS_URL must be set to run queue workers
```

**Solución**: Agregar `REDIS_URL` en variables de entorno.

### ❌ Error: Conexión a Redis Rechazada
```
ConnectionError: Error 111 connecting to redis.railway.internal:6379.
Connection refused.
```

**Solución**: 
- Verificar que servicio Redis existe y está online
- Verificar que `REDIS_URL` apunta al servicio correcto
- Usar `redis-cli` para testear conexión

---

## 🔍 Ejemplo 5: Testear Worker Localmente

### Setup Local
```bash
# 1. Clonar repo
git clone https://github.com/JeroCQ/Whatsapp-Bot.git
cd Whatsapp-Bot
git checkout "memo's-3.1"

# 2. Virtual environment
python -m venv venv
source venv/bin/activate  # En Windows: venv\Scripts\activate

# 3. Instalar dependencias
pip install -r requirements.txt

# 4. Iniciar Redis con Docker
docker run -d --name redis-local -p 6379:6379 redis:latest

# 5. Variables de entorno locales
cat > .env.local << 'EOF'
REDIS_URL=redis://localhost:6379
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_KEY=your-key-here
WA_VERIFY_TOKEN=test-token
WA_TOKEN=test-token
WA_PHONE_NUMBER_ID=1234567890
GEMINI_API_KEY=your-gemini-key
EOF

# 6. Cargar variables
set -a
source .env.local
set +a

# 7. Ejecutar worker en terminal 1
python -m workers.runner

# 8. En otra terminal (terminal 2), testear encolando tarea:
python << 'EOF'
from queue_client import enqueue, get_queue
from bot import handle_message

# Encolar una tarea
job = enqueue(
    handle_message,
    phone_number="1234567890",
    message_text="Hola bot, ¿cómo estás?",
    job_id="test-001"
)

print(f"Job enqueued: {job.id}")

# Ver queue stats
queue = get_queue()
print(f"Jobs in queue: {queue.count}")
print(f"Started jobs: {queue.started_job_registry.count}")
EOF

# 9. Ver worker procesando en terminal 1
# Debería mostrar: "Received job test-001" → "Job completed"

# 10. Limpiar
docker stop redis-local
docker rm redis-local
```

---

## 📈 Ejemplo 6: Escalar el Worker

### Opción A: Agregar Replicas (Horizontal Scaling)
```bash
# Via Railway CLI:
railway service whatsapp-worker
railway scale replicas=3

# O en Dashboard:
# Deployments → Scroll → Change "Replicas" de 1 a 3
```

**Efecto**: 3 workers procesando tareas en paralelo.

### Opción B: Ajustar Timeouts (Vertical Optimization)
```env
# Para tareas más largas (análisis de imagen, etc):
QUEUE_JOB_TIMEOUT_SECONDS=300  # 5 minutos (default: 180)
GEMINI_MAX_CONCURRENT=4        # Menos concurrencia (default: 8)

# Para limpiar resultados más rápido:
QUEUE_RESULT_TTL_SECONDS=1800  # 30 minutos (default: 3600)
```

### Opción C: Monitorear Metricas
```bash
# Via Railway API o Dashboard:
# Metrics → CPU, Memory
# - Worker: ~200-400 MB base + 50-100 MB/tarea activa
# - CPU: <5% inactivo, 20-80% bajo carga

# Via API:
curl "https://whatsapp-bot.railway.app/health/queue" | jq .
```

---

## 🔗 Ejemplo 7: Integración API ↔ Worker

### Lado API (main.py - ya existente)
```python
from fastapi import FastAPI
from queue_client import enqueue
from bot import handle_message

app = FastAPI()

@app.post("/webhooks/whatsapp")
async def whatsapp_webhook(request: dict):
    """Recibe mensaje de WhatsApp, lo encoloa al worker"""
    
    phone = request.get("phone_number")
    text = request.get("message")
    
    # Encolar tarea al worker
    job = enqueue(
        handle_message,
        phone_number=phone,
        message_text=text,
        job_id=f"msg-{phone}-{timestamp()}"
    )
    
    return {
        "status": "queued",
        "job_id": job.id
    }
```

### Lado Worker (workers/runner.py)
```python
from redis import Redis
from rq import Worker
from queue_client import QUEUE_NAME, REDIS_URL

def main():
    if not REDIS_URL:
        raise RuntimeError("REDIS_URL must be set")
    
    connection = Redis.from_url(REDIS_URL)
    worker = Worker([QUEUE_NAME], connection=connection)
    
    print(f"[WORKER] Starting RQ worker for queue={QUEUE_NAME}")
    worker.work(with_scheduler=True)  # Procesa tareas de forma continua

if __name__ == "__main__":
    main()
```

---

## 🧪 Ejemplo 8: Health Check y Debugging

### Endpoint /health/queue (en bot.py)
```python
from fastapi import FastAPI
from queue_client import get_queue_stats

app = FastAPI()

@app.get("/health/queue")
async def queue_health():
    """Retorna stats de la cola para debugging"""
    return get_queue_stats()
```

### Testear desde CLI
```bash
# Cuando el worker está online:
curl "https://whatsapp-bot.railway.app/health/queue" | jq

# Output esperado:
{
  "enabled": true,
  "queue": "whatsapp-events",
  "web_queue_mode": "external_worker",
  "queued_jobs": 3,
  "started_jobs": 1,
  "failed_jobs": 0,
  "deferred_jobs": 0,
  "workers_seen": 1
}
```

### Inspect Tareas en Redis
```bash
# Conectar directamente a Redis:
redis-cli -u $REDIS_URL

# Comandos útiles:
keys "whatsapp-events:*"           # Todas las keys de la cola
llen "whatsapp-events:queue"       # Cantidad en cola pendiente
hgetall "whatsapp-events:job:ID"   # Detalles de tarea específica
lrange "whatsapp-events:queue" 0 10  # Ver primeros 10 trabajos
```

---

## 🚨 Ejemplo 9: Manejo de Errores y Reintentos

### Worker con Reintentos (config.py)
```python
QUEUE_JOB_TIMEOUT_SECONDS = 180  # 3 minutos máximo por tarea
QUEUE_RESULT_TTL = 3600           # Guardar resultados 1 hora
QUEUE_FAILURE_TTL = 86400         # Guardar fallos 24 horas
```

### Tareas que Fallan (logs del worker)
```
ERROR: Job 567e89ab failed
redis.exceptions.ConnectionError: Connection refused
# → Worker automáticamente reintentará según ON_FAILURE policy

FAILED_JOB_REGISTRY:
- 567e89ab (fallida hace 5 min)
- 234f56cd (fallida hace 2 horas)
```

### Reintentar Tarea Manualmente
```bash
# Si una tarea falla, puede reintentar:
redis-cli -u $REDIS_URL

# En Redis CLI:
> LLEN whatsapp-events:failed
(integer) 2

# Mover de vuelta a queue (aproximado):
> RPOPLPUSH whatsapp-events:failed whatsapp-events:queue
```

---

## 📋 Ejemplo 10: Monitoreo en Producción

### Crear Script de Monitoreo
```bash
#!/bin/bash
# monitor-worker.sh

WEBHOOK_URL="https://hooks.slack.com/services/YOUR/WEBHOOK/URL"

while true; do
    STATS=$(curl -s "https://whatsapp-bot.railway.app/health/queue")
    
    WORKERS=$(echo $STATS | jq .workers_seen)
    QUEUED=$(echo $STATS | jq .queued_jobs)
    FAILED=$(echo $STATS | jq .failed_jobs)
    
    if [ "$WORKERS" -eq 0 ]; then
        curl -X POST $WEBHOOK_URL \
            -H 'Content-Type: application/json' \
            -d "{\"text\": \"⚠️ ALERTA: No hay workers online!\"}"
    fi
    
    if [ "$QUEUED" -gt 100 ]; then
        curl -X POST $WEBHOOK_URL \
            -H 'Content-Type: application/json' \
            -d "{\"text\": \"⚠️ ALERTA: Cola con $QUEUED tareas pendientes!\"}"
    fi
    
    sleep 300  # Chequear cada 5 minutos
done
```

### Ejecutar Script
```bash
chmod +x monitor-worker.sh
./monitor-worker.sh
```

---

## ✅ Checklist de Deployment

```
ANTES DE DEPLOYAR:
[ ] railway.json actualizado con startCommand correcto
[ ] REDIS_URL configurada
[ ] SUPABASE_URL y SUPABASE_KEY configuradas
[ ] Todos los secretos marcados como "secret" en Railway
[ ] requirements.txt contiene todas las dependencias

DURANTE DEPLOYMENT:
[ ] Logs muestran "[WORKER] Starting RQ worker..."
[ ] Sin errores de conexión a Redis
[ ] Sin errores de conexión a Supabase
[ ] Worker dice "Worker is ready for jobs"

DESPUÉS DE DEPLOYMENT:
[ ] /health/queue retorna stats válidos
[ ] workers_seen >= 1
[ ] Encolando tarea desde API → worker procesa
[ ] Logs sin errores continuos
[ ] Memory usage estable (~200-400 MB)
```

---

**Último actualizado**: 2026-08-18

