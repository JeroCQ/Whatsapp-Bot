"""Manual isolated-target migration entrypoint. Never called by preDeploy/startup."""
from alembic import command
from alembic.config import Config

from crm_service.pilot_app import isolated_database_url


def main() -> None:
    config = Config("alembic.ini")
    config.attributes["pilot_database_url"] = isolated_database_url()
    command.upgrade(config, "0006_scoped_pilot")


if __name__ == "__main__":
    main()
