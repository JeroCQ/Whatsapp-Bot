from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from crm_service.settings import Settings


def database(settings: Settings, *, api: bool = False) -> tuple[Engine, sessionmaker[Session]]:
    connection_args: dict[str, str | int | None] = {"connect_timeout": 5}
    options = []
    if settings.database_schema == "chatty_crm":
        options.append("-c search_path=chatty_crm")
    if api:
        # API connections must not occupy the whole bounded pool indefinitely
        # after an idle network/pooler connection stalls. Workers keep their
        # existing per-cycle SQL budgets and are not changed by these defaults.
        connection_args.update({
            "application_name": "chatty-crm-api",
            "keepalives": 1, "keepalives_idle": 30,
            "keepalives_interval": 10, "keepalives_count": 3,
            "tcp_user_timeout": 15000,
            "prepare_threshold": None,
        })
        options.extend([
            "-c statement_timeout=15000", "-c lock_timeout=2000",
            "-c idle_in_transaction_session_timeout=30000",
        ])
    if options:
        connection_args["options"] = " ".join(options)
    engine = create_engine(
        settings.database_url,
        pool_pre_ping=True,
        pool_recycle=120 if api else -1,
        pool_size=max(2, min(4, len(settings.businesses))),
        max_overflow=0,
        pool_timeout=5,
        hide_parameters=True,
        connect_args=connection_args,
    )
    return engine, sessionmaker(engine, expire_on_commit=False)
