"""Use the same sync service: scoped pilot before publication, general CRM after."""

def main():
    from crm_service.runtime_service import bootstrap, STATE
    bootstrap()
    if STATE["mode"] == "central":
        from crm_service.worker import main as run
    else:
        from crm_service.pilot_sync import main as run
    run()


if __name__ == "__main__":
    main()
