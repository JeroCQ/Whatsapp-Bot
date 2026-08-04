import os
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

GEMINI_DASHBOARD_DEFAULT_MODEL = "gemini-3.6-flash"
GEMINI_MODEL_ALIASES = {
    # Older aliases and 2.5 IDs can return 404 for new Gemini Developer API
    # projects. Normalize dashboard calls to the current concrete Flash model.
    "gemini-1.5-flash-latest": GEMINI_DASHBOARD_DEFAULT_MODEL,
    "gemini-1.5-pro-latest": GEMINI_DASHBOARD_DEFAULT_MODEL,
    "gemini-2.5-flash": GEMINI_DASHBOARD_DEFAULT_MODEL,
    "gemini-2.5-pro": GEMINI_DASHBOARD_DEFAULT_MODEL,
}


def normalize_gemini_model(model_name: str | None) -> str:
    selected = (model_name or GEMINI_DASHBOARD_DEFAULT_MODEL).strip()
    return GEMINI_MODEL_ALIASES.get(selected, selected)


def parse_gemini_model_list(raw_models: str | None, primary_model: str) -> list[str]:
    configured = [normalize_gemini_model(item) for item in (raw_models or "").split(",") if item.strip()]
    candidates = [primary_model, *configured, GEMINI_DASHBOARD_DEFAULT_MODEL, "gemini-3.5-flash", "gemini-3.1-flash-lite"]
    unique = []
    for model in candidates:
        if model and model not in unique:
            unique.append(model)
    return unique


class Settings:
    SUPABASE_URL = os.getenv("SUPABASE_URL")
    # Database access runs only on the server. Prefer the service-role secret so
    # message history is not silently hidden by RLS when SUPABASE_KEY contains a
    # publishable/anon key. Keep the old variable as a deployment-compatible
    # fallback.
    SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_KEY")

    WA_VERIFY_TOKEN = os.getenv("WA_VERIFY_TOKEN")
    WA_TOKEN = os.getenv("WA_TOKEN")
    WA_PHONE_NUMBER_ID = os.getenv("WA_PHONE_NUMBER_ID")

    CHATWOOT_BASE_URL = os.getenv("CHATWOOT_BASE_URL")
    CHATWOOT_API_TOKEN = os.getenv("CHATWOOT_API_TOKEN")
    CHATWOOT_ACCOUNT_ID = os.getenv("CHATWOOT_ACCOUNT_ID")
    CHATWOOT_INBOX_ID = os.getenv("CHATWOOT_INBOX_ID")

    # Backwards-compatible aliases for older helper code/deployments.
    # The canonical names used by this codebase are CHATWOOT_BASE_URL and
    # CHATWOOT_API_TOKEN, but these aliases prevent the audio relay from
    # crashing with AttributeError if an old call path is still imported.
    CHATWOOT_API_URL = os.getenv("CHATWOOT_API_URL") or CHATWOOT_BASE_URL
    CHATWOOT_ACCESS_TOKEN = os.getenv("CHATWOOT_ACCESS_TOKEN") or CHATWOOT_API_TOKEN

    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
    GEMINI_DASHBOARD_MODEL = normalize_gemini_model(os.getenv("GEMINI_DASHBOARD_MODEL"))
    GEMINI_DASHBOARD_MODELS = parse_gemini_model_list(os.getenv("GEMINI_DASHBOARD_FALLBACK_MODELS"), GEMINI_DASHBOARD_MODEL)

    GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
    # Railway injects repository metadata for deployments connected to GitHub.
    # Keep the manual GITHUB_* variables as optional fallbacks for local runs or
    # non-Railway deployments.
    GITHUB_OWNER = os.getenv("RAILWAY_GIT_REPO_OWNER") or os.getenv("GITHUB_OWNER")
    GITHUB_REPO = os.getenv("RAILWAY_GIT_REPO_NAME") or os.getenv("GITHUB_REPO")
    GITHUB_BRANCH = os.getenv("RAILWAY_GIT_BRANCH") or os.getenv("GITHUB_BRANCH", "main")
    DASHBOARD_API_KEY = os.getenv("DASHBOARD_API_KEY")
    DASHBOARD_CORS_ORIGINS = [
        origin.strip()
        for origin in os.getenv("DASHBOARD_CORS_ORIGINS", "").split(",")
        if origin.strip()
    ]
    DASHBOARD_REQUESTS_PER_MINUTE = int(os.getenv("DASHBOARD_REQUESTS_PER_MINUTE", "30"))
    DASHBOARD_MAX_TEXT_CHARS = int(os.getenv("DASHBOARD_MAX_TEXT_CHARS", "100000"))
    DASHBOARD_MAX_PDF_BYTES = int(os.getenv("DASHBOARD_MAX_PDF_BYTES", str(10 * 1024 * 1024)))
    DASHBOARD_EXTERNAL_TIMEOUT_SECONDS = float(os.getenv("DASHBOARD_EXTERNAL_TIMEOUT_SECONDS", "30"))
    DASHBOARD_FORMAT_TIMEOUT_SECONDS = float(os.getenv("DASHBOARD_FORMAT_TIMEOUT_SECONDS", "90"))
    DASHBOARD_HISTORY_MAX_PAGE_SIZE = int(os.getenv("DASHBOARD_HISTORY_MAX_PAGE_SIZE", "50"))

    REDIS_URL = os.getenv("REDIS_URL")
    QUEUE_NAME = os.getenv("QUEUE_NAME", "whatsapp-events")
    GEMINI_MAX_CONCURRENT = int(os.getenv("GEMINI_MAX_CONCURRENT", "8"))
    PHONE_LOCK_TTL_SECONDS = int(os.getenv("PHONE_LOCK_TTL_SECONDS", "180"))
    # JSON array of customer-facing files the model is allowed to request.
    PRESAVED_FILES_JSON = os.getenv("PRESAVED_FILES_JSON", "[]")
    # Separate catalog for Quesos Memo's so the existing deployment variable remains reusable.
    catalogo_memos = os.getenv("catalogo_memos", "[]")
    # Tanaka uses its own catalog without overwriting the reusable Memo's configuration.
    catalogo_tanaka = os.getenv("catalogo_tanaka", "[]")
    
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
