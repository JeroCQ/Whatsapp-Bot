from unittest.mock import Mock

import crm_service.db as module


def test_api_connections_are_bounded_without_increasing_pool(settings, monkeypatch):
    create = Mock()
    monkeypatch.setattr(module, "create_engine", create)
    module.database(settings, api=True)
    args = create.call_args.kwargs
    assert args["pool_size"] <= 4 and args["max_overflow"] == 0
    assert args["pool_recycle"] == 120 and args["pool_pre_ping"] is True
    connection = args["connect_args"]
    assert connection["tcp_user_timeout"] == 15000
    assert connection["prepare_threshold"] is None
    assert "statement_timeout=15000" in connection["options"]
    assert "lock_timeout=2000" in connection["options"]
    assert "idle_in_transaction_session_timeout=30000" in connection["options"]


def test_worker_policy_is_not_replaced_by_api_deadlines(settings, monkeypatch):
    create = Mock()
    monkeypatch.setattr(module, "create_engine", create)
    module.database(settings)
    args = create.call_args.kwargs
    assert args["pool_recycle"] == -1
    assert "statement_timeout" not in args["connect_args"].get("options", "")
    assert "tcp_user_timeout" not in args["connect_args"]
