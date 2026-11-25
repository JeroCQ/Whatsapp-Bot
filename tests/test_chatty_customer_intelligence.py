import json
from types import SimpleNamespace

from customer_insights import analyze_return
from promotion_permission import buttons, choice_from_message

CUTOFF = "2026-11-25T12:00:00+00:00"


def test_promotion_buttons_and_text_choices_have_expected_scope():
    yes = buttons()["action"]["buttons"][0]["reply"]["id"]
    payload = {"type": "interactive", "interactive": {"type": "button_reply", "button_reply": {"id": yes}}}
    assert choice_from_message(payload) is None
    context = {"id": "wamid.question"}
    assert choice_from_message({**payload, "context": context}) == ("granted", "wamid.question")
    assert choice_from_message({"type": "text", "text": {"body": "Sí"}}) is None
    assert choice_from_message({"type": "text", "text": {"body": "No, gracias"}, "context": context}) == (
        "denied", "wamid.question"
    )
    assert choice_from_message({"type": "text", "text": {"body": "BAJA PROMOCIONES"}}) == ("revoked", None)


class Source:
    def __init__(self, orders):
        self.rows = {
            "orders": orders,
            "customers": [{"phone_number": "test-contact", "id": "record-1", "created_at": CUTOFF}],
            "conversation_states": [{"phone_number": "test-contact", "customer_data": {"tipo_cliente": "detal"},
                                     "order_summary": "Consulta previa"}],
            "message_logs": [{"phone_number": "test-contact", "id": index, "created_at": CUTOFF,
                              "role": "asesor" if index == 251 else "user", "content": f"Mensaje {index}"}
                             for index in range(1, 252)],
        }

    def table(self, name):
        return Query(self.rows[name])


class Query:
    def __init__(self, rows):
        self.rows = rows
        self.count = False
        self.head = False

    def select(self, _fields, count=None, head=False):
        self.count, self.head = count, head
        return self

    def eq(self, field, value):
        self.rows = [row for row in self.rows if row[field] == value]
        return self

    def gt(self, field, value):
        self.rows = [row for row in self.rows if row[field] > value]
        return self

    def lte(self, field, value):
        self.rows = [row for row in self.rows if row[field] <= value]
        return self

    def order(self, field):
        self.rows = sorted(self.rows, key=lambda row: row[field])
        return self

    def limit(self, count):
        self.rows = self.rows[:count]
        return self

    def execute(self):
        return SimpleNamespace(data=[] if self.head else self.rows,
                               count=len(self.rows) if self.count else None)


def test_reads_saved_context_all_history_pages_and_never_infers_order_count():
    received = []

    def synthetic_model(_instruction, payload):
        data = json.loads(payload)
        received.extend(message["id"] for message in data["messages"])
        assert data["saved_profile"]["saved_order_summary"] == "Consulta previa"
        return json.dumps({"summary": "Se confirmó un pedido en el chat", "order_confirmed_ids": [251, 9999]})

    result = analyze_return(Source([]), "test-contact", CUTOFF, synthetic_model)
    assert received == list(range(1, 252))
    assert result["confirmed_order_count"] is None
    assert result["labels"] == ["cliente_con_pedido"]
    assert result["evidence"]["order_confirmed_ids"] == [251]


def test_confirmed_order_count_is_read_from_customer_records():
    orders = [{"customer_id": "record-1", "status": status, "created_at": CUTOFF}
              for status in ("CONFIRMED", "CONFIRMED", "PENDING_PAYMENT")]
    result = analyze_return(Source(orders), "test-contact", CUTOFF,
                            lambda *_: '{"summary":"Cliente con historial"}')
    assert result["confirmed_order_count"] == 2
    assert result["labels"] == ["cliente_recurrente", "cliente_con_pedido"]
