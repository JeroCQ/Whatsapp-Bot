import os
import logging

# Configuración básica de logs para ver errores en el dashboard de Railway o en Colab
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

class Settings:
    # --- SUPABASE ---
    SUPABASE_URL = os.getenv("SUPABASE_URL")
    # 🚨 Importante: En Railway, usa la "service_role_key" de Supabase para que FastAPI 
    # tenga permisos de escritura en todas las tablas sin lidiar con RLS por ahora.
    SUPABASE_KEY = os.getenv("SUPABASE_KEY") 

    # --- WHATSAPP CLOUD API ---
    WA_VERIFY_TOKEN = os.getenv("WA_VERIFY_TOKEN")  # Token inventado por ti (Ej: "mi_token_secreto_123")
    WA_TOKEN = os.getenv("WA_TOKEN")                # El Token de acceso temporal o permanente de Meta
    WA_PHONE_NUMBER_ID = os.getenv("WA_PHONE_NUMBER_ID")

    # --- CHATWOOT ---
    CHATWOOT_BASE_URL = os.getenv("CHATWOOT_BASE_URL") # Ej: https://app.chatwoot.com
    CHATWOOT_API_TOKEN = os.getenv("CHATWOOT_API_TOKEN")
    CHATWOOT_ACCOUNT_ID = os.getenv("CHATWOOT_ACCOUNT_ID")
    CHATWOOT_INBOX_ID = os.getenv("CHATWOOT_INBOX_ID")

    @classmethod
    def validate(cls):
        """
        Valida que las variables críticas existan al arrancar FastAPI.
        Si falta alguna en Railway, el despliegue fallará y te avisará en los logs,
        evitando errores silenciosos en producción.
        """
        critical_vars = [
            "SUPABASE_URL", "SUPABASE_KEY", 
            "WA_VERIFY_TOKEN", "WA_TOKEN", "WA_PHONE_NUMBER_ID"
        ]
        missing = [var for var in critical_vars if not getattr(cls, var)]
        
        if missing:
            error_msg = f"FALTAN VARIABLES DE ENTORNO CRÍTICAS: {', '.join(missing)}. ¡Configúralas en Railway/Colab!"
            logger.error(error_msg)
            raise ValueError(error_msg)

# Instanciamos la configuración para importarla en otros archivos
config = Settings()

# Validamos inmediatamente al importar
config.validate()
