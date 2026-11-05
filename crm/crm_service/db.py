from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from crm_service.settings import Settings


def database(settings: Settings) -> tuple[Engine, sessionmaker[Session]]:
    engine = create_engine(
        settings.database_url,
        pool_pre_ping=True,
        pool_size=max(2, min(4, len(settings.businesses))),
        max_overflow=0,
        pool_timeout=5,
        hide_parameters=True,
        connect_args={"connect_timeout": 5},
    )
    return engine, sessionmaker(engine, expire_on_commit=False)
