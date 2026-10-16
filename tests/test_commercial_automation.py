from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

from commercial_intents import (
    DEFAULT_CATALOG_FOLLOW_UP_MESSAGE,
    b2b_actions,
    b2b_reason,
    follow_up_plan,
    is_terminal_customer_message,
    normalize_follow_up_message,
)
from queue_client import follow_up_delay_seconds


def test_maquila_and_distribution_are_deterministic_b2b_synonyms():
    assert b2b_reason("Quisiera saber si uds maquilan productos") == "B2B_HIGH_VALUE: interés en maquila"
    assert b2b_reason("Me gustaría ser distribuidor") == "B2B_HIGH_VALUE: interés en distribución"
    for text in (
        "fabricación para terceros", "producción para terceros", "marca blanca",
        "private label", "mayorista", "venta al por mayor", "revendedor",
        "compra empresarial", "grandes cantidades para mi negocio",
    ):
        assert b2b_reason(text), text


def test_maquila_and_distributor_each_emit_one_catalog_and_one_handoff_reason():
    available = {"catalogo_tanaka", "catalogo_distibuidores"}
    for text in ("Quisiera saber si uds maquilan productos", "Me gustaría ser distribuidor"):
        reason, files = b2b_actions(text, available)
        assert reason and reason.startswith("B2B_HIGH_VALUE:")
        assert files == ["catalogo_distibuidores"]


def test_catalog_stage_replaces_first_response_and_has_three_attempts():
    first = follow_up_plan("Hola, ¿qué buscas?", [], "")
    catalog = follow_up_plan("Te comparto opciones", ["catalogo_distibuidores"], "")
    assert first.stage == "FIRST_RESPONSE"
    assert first.delays_minutes == (10, 90, -1)
    assert catalog.stage == "CATALOG_SENT"
    assert catalog.delays_minutes == (15, 120, -1)
    assert all("catálogo" not in message.lower() or "viste" in message.lower() for message in catalog.messages)


@pytest.mark.parametrize(
    ("catalog_id", "custom_message"),
    [
        ("catalogo_memos", "¿Qué queso te interesa y cuántas libras necesitas?"),
        ("catalogo_mochis", "¿Prefieres tus mochis para probar o para compartir?"),
        ("catalogo_tanaka", "¿Cuántos paquetes de arepas quisieras llevar?"),
        ("catalogo_acme", "¿Qué modelo de lámpara te gustó para tu espacio?"),
    ],
    ids=["memos", "velvet", "tanaka", "future_business"],
)
def test_catalog_follow_up_keeps_each_business_custom_message(catalog_id, custom_message):
    plan = follow_up_plan("Te comparto las opciones", [catalog_id], f"  {custom_message}\n")

    assert plan.stage == "CATALOG_SENT"
    assert plan.delays_minutes == (15, 120, -1)
    assert plan.messages == (custom_message,) * 3


@pytest.mark.parametrize("model_message", ["", " \n\t "])
def test_catalog_follow_up_without_custom_text_uses_business_neutral_fallback(model_message):
    plan = follow_up_plan("Te comparto las opciones", ["catalogo_acme"], model_message)

    assert plan.messages == (DEFAULT_CATALOG_FOLLOW_UP_MESSAGE,) * 3
    assert "panadería" not in DEFAULT_CATALOG_FOLLOW_UP_MESSAGE.lower()
    assert "postres" not in DEFAULT_CATALOG_FOLLOW_UP_MESSAGE.lower()
    assert "precio" not in DEFAULT_CATALOG_FOLLOW_UP_MESSAGE.lower()


def test_follow_up_normalization_preserves_legitimate_custom_bakery_context():
    custom_message = "¿Cuántos kilos de queso necesitas para tu panadería?"

    assert normalize_follow_up_message(custom_message) == custom_message
    plan = follow_up_plan("Te comparto nuestros quesos", ["catalogo_memos"], custom_message)
    assert plan.messages == (custom_message,) * 3


def test_catalog_follow_up_replaces_exact_obsolete_model_text():
    obsolete_message = (
        "¿Viste alguna opción que te gustara? Dime si prefieres panadería o postres "
        "y te recomiendo dos opciones con precio 😊"
    )
    plan = follow_up_plan("Te comparto las opciones", ["catalogo_memos"], obsolete_message)

    assert plan.messages == (DEFAULT_CATALOG_FOLLOW_UP_MESSAGE,) * 3


def test_incomplete_and_payment_stages_use_aggressive_cadences_and_context():
    incomplete = follow_up_plan("¿En qué barrio estás?", [], "Solo me falta tu barrio. ¿Dónde te encuentras?")
    payment = follow_up_plan("Tu total es $32.000. ¿Cómo deseas pagar?", [], "")
    assert incomplete.stage == "INCOMPLETE_INTENT"
    assert incomplete.delays_minutes == (10, 45, 180)
    assert incomplete.messages[0] == "Solo me falta tu barrio. ¿Dónde te encuentras?"
    assert payment.stage == "PAYMENT_PENDING"
    assert payment.delays_minutes == (8, 30, 120)


def test_deferred_delivery_is_resumed_at_next_service_opening():
    plan = follow_up_plan("Te atendemos mañana", [], "")
    assert plan.stage == "DEFERRED_DELIVERY"
    assert plan.delays_minutes == (-1,)


def test_rejection_payment_and_close_are_terminal():
    for text in ("No gracias", "No me escriban", "Ya pagué", "Adjunto el comprobante", "Pedido confirmado", "venta cerrada"):
        assert is_terminal_customer_message(text)


def test_service_window_moves_attempt_and_never_sends_at_night():
    now = datetime(2026, 9, 29, 17, 55, tzinfo=ZoneInfo("America/Bogota"))
    assert follow_up_delay_seconds(10, {}, now) == 14 * 60 * 60 + 5 * 60


def test_attempt_claim_is_idempotent_and_sequence_token_stays_active():
    import queue_client

    class Redis:
        def __init__(self):
            self.current = "token"
            self.claimed = set()

        def eval(self, _script, _count, active_key, attempt_key, token, _ttl):
            if self.current != token or attempt_key in self.claimed:
                return 0
            self.claimed.add(attempt_key)
            return 1

    queue = type("Queue", (), {"connection": Redis()})()
    with patch.object(queue_client, "get_queue", return_value=queue):
        assert queue_client.claim_follow_up_attempt("57300", "token", "CATALOG_SENT", 1)
        assert not queue_client.claim_follow_up_attempt("57300", "token", "CATALOG_SENT", 1)
        assert queue_client.claim_follow_up_attempt("57300", "token", "CATALOG_SENT", 2)
