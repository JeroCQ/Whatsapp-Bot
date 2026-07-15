import os
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

class Settings:
    SUPABASE_URL = os.getenv("SUPABASE_URL")
    SUPABASE_KEY = os.getenv("SUPABASE_KEY") 

    WA_VERIFY_TOKEN = os.getenv("WA_VERIFY_TOKEN")
    WA_TOKEN = os.getenv("WA_TOKEN")
    WA_PHONE_NUMBER_ID = os.getenv("WA_PHONE_NUMBER_ID")

    CHATWOOT_BASE_URL = os.getenv("CHATWOOT_BASE_URL")
    CHATWOOT_API_TOKEN = os.getenv("CHATWOOT_API_TOKEN")
    CHATWOOT_ACCOUNT_ID = os.getenv("CHATWOOT_ACCOUNT_ID")
    CHATWOOT_INBOX_ID = os.getenv("CHATWOOT_INBOX_ID")

    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
    
    @classmethod
    def validate(cls):
        critical_vars = [
            "SUPABASE_URL", "SUPABASE_KEY", 
            "WA_VERIFY_TOKEN", "WA_TOKEN", "WA_PHONE_NUMBER_ID",
            "GEMINI_API_KEY"
        ]
        missing = [var for var in critical_vars if not getattr(cls, var)]
        if missing:
            error_msg = f"FALTAN VARIABLES DE ENTORNO CRÍTICAS: {', '.join(missing)}"
            logger.error(error_msg)
            raise ValueError(error_msg)

config = Settings()
config.validate()
