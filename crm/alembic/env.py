import os

from sqlalchemy import create_engine

from alembic import context
from crm_service.models import Base

config = context.config
central = config.attributes.get("central_connection")
url = config.attributes.get("pilot_database_url")
if central is None:
    url = url or os.environ["CRM_DATABASE_URL"]
if central is None and not url.startswith("postgresql+psycopg://"):
    raise ValueError("Migrations target the independent CRM PostgreSQL database only")
if central is None:
    from crm_service.central_copy_connection import is_central_project
    if os.environ.get("CRM_DATABASE_SCHEMA") == "chatty_crm" or is_central_project(url):
        raise ValueError("Use the dedicated central CRM migration command for Tanaka")

if central is not None:
    context.configure(connection=central, target_metadata=Base.metadata,
                      version_table_schema=config.attributes["central_schema"], compare_type=True)
    with context.begin_transaction():
        context.run_migrations()
elif context.is_offline_mode():
    context.configure(url=url, target_metadata=Base.metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    engine = create_engine(url)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=Base.metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()
