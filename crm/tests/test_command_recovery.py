"""Targeted takeover/404 regressions. Bot calls are synthetic, CRM uses disposable PostgreSQL."""

import httpx
import pytest
from fastapi import HTTPException

from crm_service.bot_commands import CommandCoordinator
from crm_service.bot_gateway import BotGateway, BotGatewayError
from crm_service.models import BotCommand
from crm_service.security import Actor
from crm_service.settings import Business

ACTOR, PHONE, COMMAND = "test.admin:tanaka", "573000000001", "same-command"


@pytest.mark.parametrize("operation,phase,owner,expected", [
    ("take", "human", ACTOR, "confirmed"),
    ("take", "pausing", ACTOR, "unknown"),
    ("take", "human", "test.admin", "unknown"),
    ("messages", "human", ACTOR, "unknown"),
])
def test_missing_command_reconciles_only_control_not_message_acceptance(sessions, operation, phase, owner, expected):
    calls = []
    control = {"business_id": "tanaka", "phone_number": PHONE, "provider": "crm", "phase": phase,
               "version": 8, "owner": owner, "is_paused": True, "confirmed": phase == "human",
               "can_send": False, "window_expires_at": "2020-01-01T00:00:00Z"}

    def respond(request):
        calls.append((request.method, request.url.path))
        assert request.method == "GET", "Recovery must never repeat the mutation"
        if "/commands/" in request.url.path:
            assert request.url.path.endswith("/" + COMMAND)
            return httpx.Response(404, json={"detail": "command_not_found"})
        assert request.url.path.endswith("/control")
        return httpx.Response(200, json=control)

    business = Business("tanaka", "", "", bot_api_url="https://synthetic.invalid",
                        bot_control_secret="synthetic-only-" + "x" * 40, operations_enabled=True)
    with sessions.begin() as session:
        session.add(BotCommand(business_id="tanaka", command_id=COMMAND, phone_number=PHONE, profile=ACTOR,
                               operation=operation, request_hash="a" * 64, status="unknown",
                               response={"command_id": COMMAND, "status": "unknown", "control": None}, http_status=202))
    gateway = BotGateway(business, transport=httpx.MockTransport(respond))
    try:
        coordinator = CommandCoordinator(sessions, gateway)
        for _ in range(2):
            result, status = coordinator.status(Actor(ACTOR, ("tanaka",)), PHONE, COMMAND)
            assert result["command_id"] == COMMAND and result["status"] == expected
            assert result["control"] == control and result["control"]["can_send"] is False
            assert status == (200 if expected == "confirmed" else 202)
            if expected == "unknown":
                assert result["error"] == "command_result_missing_no_resend"
        with pytest.raises(HTTPException):
            coordinator.status(Actor(ACTOR, ("velvet",)), PHONE, COMMAND)
        with pytest.raises(BotGatewayError, match="command_not_found"):
            coordinator.status(Actor(ACTOR, ("tanaka",)), "573000000002", COMMAND)
        assert len(calls) == 4
    finally:
        gateway.close()


def test_generic_404_is_not_a_missing_command_proof():
    business = Business("tanaka", "", "", bot_api_url="https://synthetic.invalid",
                        bot_control_secret="synthetic-only-" + "x" * 40, operations_enabled=True)
    gateway = BotGateway(business, transport=httpx.MockTransport(lambda _: httpx.Response(404, json={"detail": "Not found"})))
    try:
        with pytest.raises(BotGatewayError, match="bot_rejected_404"):
            gateway.command(PHONE, COMMAND)
    finally:
        gateway.close()
