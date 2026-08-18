# 🚀 COPY-PASTE: Configuración Lista para el Worker

Usa este archivo para copiar y pegar configuraciones directamente sin tener que escribir desde cero.

---

## 1️⃣ railway.json (ACTUALIZADO)

**Archivo**: `railway.json` (reemplazar completamente)

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

**Cómo usar**:
1. Abrir archivo `railway.json` en repo
2. Copiar contenido anterior COMPLETAMENTE
3. Pegar este JSON
4. Guardar y hacer commit

---

## 2️⃣ Variables de Entorno (COPIAR A RAILWAY)

En Railway Dashboard → `whatsapp-worker` service → Variables:

```
REDIS_URL=${{ Redis.REDIS_URL }}
QUEUE_NAME=whatsapp-events
QUEUE_JOB_TIMEOUT_SECONDS=180
QUEUE_RESULT_TTL_SECONDS=3600
QUEUE_FAILURE_TTL_SECONDS=86400
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_KEY=eyJhbGciOiJIUzI1NiIs...
WA_VERIFY_TOKEN=your-verify-token
WA_TOKEN=EAABGd...
WA_PHONE_NUMBER_ID=1234567890
GEMINI_API_KEY=AIzaSyD...
GEMINI_MAX_CONCURRENT=8
CHATWOOT_BASE_URL=https://chatwoot.example.com
CHATWOOT_API_TOKEN=your-chatwoot-token
CHATWOOT_ACCOUNT_ID=1
CHATWOOT_INBOX_ID=1
PHONE_LOCK_TTL_SECONDS=180
PRESAVED_FILES_JSON=[]
catalogo_memos=[]
```

**Cómo usar**:
1. Abrir Railway Dashboard
2. Ir a `whatsapp-worker` → Variables
3. Click "Add Variable" para cada línea
4. O descargar JSON e importar en batch

---

## 3️⃣ Start Command (RAILWAY UI)

En Railway Dashboard → `whatsapp-worker` → Deployments → Settings:

**Start Command**:
```
python -m workers.runner
```

O si necesita build step:
```
python -m py_compile main.py chatwoot_api.py config.py database.py queue_client.py processing_lock.py workers/runner.py run_railway.py && python -m workers.runner
```

---

## 4️⃣ Procfile (OPCIONAL - para Heroku compatibility)

**Archivo**: `Procfile` (ya existe, actualizar si es necesario)

```procfile
web: python -m py_compile main.py chatwoot_api.py config.py database.py queue_client.py processing_lock.py workers/runner.py run_railway.py && python run_railway.py
worker: python -m workers.runner
```

---

## 5️⃣ requirements.txt (VERIFICAR)

**Archivo**: `requirements.txt` (ya existe, solo verificar)

```
fastapi>=0.109.0
uvicorn>=0.27.0
requests>=2.31.0
pydantic>=2.5.0
supabase>=2.0.0
google-genai>=0.5.0
redis>=5.0.0
rq>=1.14.0
```

---

## 6️⃣ Docker Compose LOCAL (para testear antes de deployar)

**Archivo**: `docker-compose.yml` (crear en raíz del proyecto)

```yaml
version: '3.8'

services:
  redis:
    image: redis:latest
    ports:
      - "6379:6379"
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 3s
      retries: 5

  postgres:
    image: postgres:15
    environment:
      POSTGRES_USER: whatsapp_user
      POSTGRES_PASSWORD: whatsapp_pass
      POSTGRES_DB: whatsapp_db
    ports:
      - "5432:5432"
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U whatsapp_user"]
      interval: 5s
      timeout: 3s
      retries: 5

  worker:
    build: .
    command: python -m workers.runner
    depends_on:
      redis:
        condition: service_healthy
    environment:
      REDIS_URL: redis://redis:6379
      DATABASE_URL: postgresql://whatsapp_user:whatsapp_pass@postgres:5432/whatsapp_db
      SUPABASE_URL: ${SUPABASE_URL}
      SUPABASE_KEY: ${SUPABASE_KEY}
      WA_VERIFY_TOKEN: ${WA_VERIFY_TOKEN}
      WA_TOKEN: ${WA_TOKEN}
      WA_PHONE_NUMBER_ID: ${WA_PHONE_NUMBER_ID}
      GEMINI_API_KEY: ${GEMINI_API_KEY}
    volumes:
      - .:/app
    working_dir: /app
```

**Cómo usar**:
```bash
# 1. Crear .env local
cat > .env << 'EOF'
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_KEY=your-key
WA_VERIFY_TOKEN=your-token
WA_TOKEN=your-token
WA_PHONE_NUMBER_ID=123456
GEMINI_API_KEY=your-key
EOF

# 2. Ejecutar
docker-compose up -d

# 3. Ver logs
docker-compose logs -f worker

# 4. Parar
docker-compose down
```

---

## 7️⃣ Script de Test (encolar tarea)

**Archivo**: `test_enqueue.py` (crear en raíz)

```python
#!/usr/bin/env python3
"""
Script para testear encolado de tareas.
Uso: python test_enqueue.py
"""

import os
import sys
import json
from datetime import datetime

# Validar variables
required_vars = ["REDIS_URL", "SUPABASE_URL", "SUPABASE_KEY", "GEMINI_API_KEY"]
missing = [v for v in required_vars if not os.getenv(v)]
if missing:
    print(f"❌ Faltan variables: {', '.join(missing)}")
    sys.exit(1)

from queue_client import enqueue, get_queue, get_queue_stats
from bot import handle_message

def test_enqueue():
    """Encolar una tarea de prueba"""
    
    print("📤 Encolando tarea de prueba...")
    
    # Crear tarea
    job = enqueue(
        handle_message,
        phone_number="1234567890",
        message_text="Hola bot, ¿cómo estás? 🤖",
        job_id=f"test-{datetime.now().isoformat()}"
    )
    
    print(f"✅ Tarea encolada exitosamente")
    print(f"   Job ID: {job.id}")
    print(f"   Estado: {job.get_status()}")
    
    # Mostrar stats
    print("\n📊 Stats de la cola:")
    stats = get_queue_stats()
    print(json.dumps(stats, indent=2))
    
    return job.id

def test_queue_connection():
    """Testear conexión a la cola"""
    print("🔌 Testeando conexión a Redis...")
    
    try:
        queue = get_queue()
        if not queue:
            print("❌ No hay cola configurada (REDIS_URL vacío)")
            return False
        
        ping = queue.connection.ping()
        print(f"✅ Redis OK: {ping}")
        return True
    except Exception as e:
        print(f"❌ Error: {e}")
        return False

if __name__ == "__main__":
    print("=" * 60)
    print("WORKER ENQUEUE TEST")
    print("=" * 60)
    
    # Test 1: Conexión
    if not test_queue_connection():
        sys.exit(1)
    
    # Test 2: Encolar
    job_id = test_enqueue()
    
    print("\n" + "=" * 60)
    print("✨ Ahora el worker debería procesar esta tarea")
    print(f"Monitorear con: railway logs --follow")
    print("=" * 60)
```

**Cómo usar**:
```bash
# Setup
export REDIS_URL=redis://localhost:6379
export SUPABASE_URL=https://...
export SUPABASE_KEY=...
export GEMINI_API_KEY=...

# Ejecutar
python test_enqueue.py
```

---

## 8️⃣ Script de Monitoreo

**Archivo**: `monitor_worker.py` (crear en raíz)

```python
#!/usr/bin/env python3
"""
Monitor del worker - verifica health cada 10 segundos.
Uso: python monitor_worker.py
"""

import os
import time
import json
from queue_client import get_queue_stats

def print_stats(stats):
    """Imprimir stats de forma amigable"""
    print("\n" + "=" * 60)
    print(f"⏰ {time.strftime('%H:%M:%S')}")
    
    if not stats.get("enabled"):
        print("❌ Cola NO configurada")
        return
    
    print(f"✅ Cola: {stats.get('queue')}")
    print(f"   Workers online: {stats.get('workers_seen', 0)}")
    print(f"   📥 En cola: {stats.get('queued_jobs', 0)}")
    print(f"   ▶️  En progreso: {stats.get('started_jobs', 0)}")
    print(f"   ✅ Completadas (cached): {stats.get('completed_jobs', 0)}")
    print(f"   ❌ Fallidas: {stats.get('failed_jobs', 0)}")
    print(f"   ⏳ Diferidas: {stats.get('deferred_jobs', 0)}")
    
    if stats.get('error'):
        print(f"   ⚠️  Error: {stats.get('error')}")
    
    print("=" * 60)

def monitor():
    """Loop de monitoreo"""
    interval = int(os.getenv("MONITOR_INTERVAL", "10"))
    
    print("🔍 Iniciando monitoreo de worker...")
    print(f"   Intervalo: {interval}s")
    print("   Press Ctrl+C para salir\n")
    
    try:
        while True:
            stats = get_queue_stats()
            print_stats(stats)
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\n\n👋 Monitoreo detenido")

if __name__ == "__main__":
    monitor()
```

**Cómo usar**:
```bash
# Ejecutar en terminal separada
MONITOR_INTERVAL=5 python monitor_worker.py
```

---

## 9️⃣ .env.example (para documentar variables)

**Archivo**: `.env.example` (crear en raíz, NO commitear valores reales)

```env
# Redis
REDIS_URL=redis://localhost:6379

# Database
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_KEY=eyJhbGciOiJIUzI1NiIs...

# WhatsApp
WA_VERIFY_TOKEN=your-verify-token
WA_TOKEN=EAABGd...
WA_PHONE_NUMBER_ID=1234567890

# Google Gemini
GEMINI_API_KEY=AIzaSyD...
GEMINI_MAX_CONCURRENT=8

# Chatwoot (opcional)
CHATWOOT_BASE_URL=https://chatwoot.example.com
CHATWOOT_API_TOKEN=your-token
CHATWOOT_ACCOUNT_ID=1
CHATWOOT_INBOX_ID=1

# Queue
QUEUE_NAME=whatsapp-events
QUEUE_JOB_TIMEOUT_SECONDS=180
QUEUE_RESULT_TTL_SECONDS=3600
QUEUE_FAILURE_TTL_SECONDS=86400

# Locks
PHONE_LOCK_TTL_SECONDS=180

# Files
PRESAVED_FILES_JSON=[]
catalogo_memos=[]
```

**Cómo usar**:
```bash
# Copiar y llenar con valores reales
cp .env.example .env
nano .env  # Editar con valores reales
```

---

## 🔟 GitHub Actions CI/CD (OPCIONAL)

**Archivo**: `.github/workflows/test-worker.yml` (crear)

```yaml
name: Worker Tests

on:
  push:
    branches: [main, "memo's-3.1"]
  pull_request:
    branches: [main, "memo's-3.1"]

jobs:
  test:
    runs-on: ubuntu-latest
    
    services:
      redis:
        image: redis:latest
        options: >-
          --health-cmd "redis-cli ping"
          --health-interval 10s
          --health-timeout 5s
          --health-retries 5
        ports:
          - 6379:6379
    
    steps:
      - uses: actions/checkout@v3
      
      - name: Set up Python
        uses: actions/setup-python@v4
        with:
          python-version: '3.11'
      
      - name: Install dependencies
        run: |
          python -m pip install --upgrade pip
          pip install -r requirements.txt
      
      - name: Lint with flake8
        run: |
          pip install flake8
          flake8 workers/ --count --select=E9,F63,F7,F82 --show-source --statistics
      
      - name: Test imports
        run: |
          python -m py_compile main.py
          python -m py_compile workers/runner.py
          python -m py_compile queue_client.py
      
      - name: Test queue connection
        env:
          REDIS_URL: redis://localhost:6379
          SUPABASE_URL: ${{ secrets.SUPABASE_URL }}
          SUPABASE_KEY: ${{ secrets.SUPABASE_KEY }}
          GEMINI_API_KEY: ${{ secrets.GEMINI_API_KEY }}
          WA_VERIFY_TOKEN: test
          WA_TOKEN: test
          WA_PHONE_NUMBER_ID: test
        run: |
          python << 'EOF'
          from queue_client import get_queue, queue_enabled
          assert queue_enabled(), "Queue no configurada"
          queue = get_queue()
          assert queue is not None, "Cola es None"
          print("✅ Queue test passed")
          EOF
```

---

## 📋 CHECKLIST RÁPIDO

Copia y pega este checklist para verificar que todo está listo:

```
ANTES DE DEPLOYAR:

▢ railway.json actualizado (startCommand = python -m workers.runner)
▢ requirements.txt con todas las deps
▢ Variables configuradas en Railway:
  ▢ REDIS_URL = ${{ Redis.REDIS_URL }}
  ▢ SUPABASE_URL y SUPABASE_KEY
  ▢ Todos los tokens de WhatsApp
  ▢ GEMINI_API_KEY (marked as secret)
  ▢ Variables de timeouts
▢ Secretos marcados como "secret" en Railway
▢ Redis service existe y está online

DURANTE DEPLOYMENT:

▢ Logs muestran: "[WORKER] Starting RQ worker..."
▢ Sin errores de conexión a Redis
▢ Sin errores de conexión a BD
▢ Worker dice "Worker is ready for jobs"

DESPUÉS DE DEPLOYMENT:

▢ /health/queue retorna stats
▢ workers_seen >= 1
▢ Encolando tarea → Worker procesa
▢ Logs sin errores continuos
▢ Memory ~200-400 MB estable
```

---

**Última actualización**: 2026-08-18
**Tamaño**: Copy-paste listo para usar ✅

