"""Keep the verified pilot factory until its coordinated central publication."""
import os


def create_app():
    from crm_service.runtime_service import bootstrap, STATE
    bootstrap()
    if STATE["mode"] == "central":
        from crm_service.api import create_app as general_app
        app = general_app()
    else:
        from crm_service.pilot_app import create_app as pilot_app
        app = pilot_app()
    if os.environ.get("CHATTY_RUNTIME_REGISTRY_ENABLED") == "true":
        from crm_service.central_migration_jobs import create_router
        app.include_router(create_router())
    return app
