from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from crm_service.settings import Settings


def database(settings: Settings) -> tuple[Engine, sessionmaker[Session]]:
    connection_args: dict[str, str | int] = {"connect_timeout": 5}
    if settings.database_schema == "chatty_crm":
        connection_args["options"] = "-c search_path=chatty_crm"
    engine = create_engine(
        settings.database_url,
        pool_pre_ping=True,
        pool_size=max(2, min(4, len(settings.businesses))),
        max_overflow=0,
        pool_timeout=5,
        hide_parameters=True,
        connect_args=connection_args,
    )
    return engine, sessionmaker(engine, expire_on_commit=False)
