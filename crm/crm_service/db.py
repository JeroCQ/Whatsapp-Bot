from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from crm_service.settings import Settings


def database(settings: Settings) -> tuple[Engine, sessionmaker[Session]]:
    engine = create_engine(
        settings.database_url,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=5,
        hide_parameters=True,
        connect_args={"connect_timeout": 10},
    )
    return engine, sessionmaker(engine, expire_on_commit=False)
