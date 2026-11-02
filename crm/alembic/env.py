import os

from sqlalchemy import create_engine

from alembic import context
from crm_service.models import Base

config = context.config
url = os.environ["CRM_DATABASE_URL"]
if not url.startswith("postgresql+psycopg://"):
    raise ValueError("Migrations target the independent CRM PostgreSQL database only")

if context.is_offline_mode():
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
