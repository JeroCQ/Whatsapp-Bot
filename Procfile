web: python -m py_compile main.py chatwoot_api.py config.py database.py queue_client.py processing_lock.py workers/runner.py && uvicorn main:app --host 0.0.0.0 --port ${PORT:-8080}
worker: python -m workers.runner
