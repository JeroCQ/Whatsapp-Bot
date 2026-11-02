"""Persist transcript provenance on the original event without extra incoming rows."""

import copy
import importlib.util
from pathlib import Path
import sys
import types
from unittest.mock import patch

import pytest

from conversation_summary import format_handoff_history


@pytest.fixture
def transcript_database():
    rows, queries = [], []

    class Query:
        def __init__(self, table):
            assert table == "message_logs"
            self.filters = {}
            self.values = None

        def select(self, columns):
            assert columns == "id,metadata"
            return self

        def update(self, values):
            self.values = values
            return self

        def eq(self, key, value):
            self.filters[key] = value
            return self

        def execute(self):
            queries.append((copy.deepcopy(self.filters), copy.deepcopy(self.values)))
            matches = [row for row in rows if all(
                ((row.get("metadata") or {}).get("source_event_id") if key == "metadata->>source_event_id" else row.get(key)) == value
                for key, value in self.filters.items())]
            if self.values is not None:
                for row in matches:
                    row.update(copy.deepcopy(self.values))
            return types.SimpleNamespace(data=copy.deepcopy(matches))

    client = types.SimpleNamespace(table=lambda name: Query(name))
    fake_config = types.ModuleType("config")
    fake_config.config = types.SimpleNamespace(SUPABASE_URL="https://synthetic.supabase.co", SUPABASE_KEY="synthetic-key")
    fake_supabase = types.ModuleType("supabase")
    fake_supabase.Client = object
    fake_supabase.create_client = lambda *args: client
    fake_observability = types.ModuleType("observability")
    fake_observability.message_metadata = lambda role: {
        "source": "incoming", "source_event_id": "trace-event",
        "received_at": "2026-10-22T16:00:00Z", "job_id": "transcribing-job",
        "business_id": "future_brand", "deployment_sha": "synthetic-sha",
    }
    fake_observability.message_event_key = lambda *args: None
    spec = importlib.util.spec_from_file_location("_inbound_transcript_database_test", Path(__file__).resolve().parents[1] / "database.py")
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {"config": fake_config, "supabase": fake_supabase, "observability": fake_observability}):
        spec.loader.exec_module(module)
    return types.SimpleNamespace(module=module, rows=rows, queries=queries)


def incoming(identifier, *, phone="CO.SyntheticSender", event="wamid.voice", role="user"):
    return {"id": identifier, "phone_number": phone, "role": role, "content": "[Archivo/audio] [Audio Nota]",
            "metadata": {"source": "incoming", "source_event_id": event,
                         "received_at": "2026-10-22T14:44:29Z", "business_id": "future_brand",
                         "original_custom_field": "preserved"}}


def test_transcription_updates_only_original_incoming_event_and_preserves_original_window(transcript_database):
    db = transcript_database
    db.rows.extend([incoming(1), incoming(2, phone="CO.OtherSender"), incoming(3, event="wamid.other"),
                    incoming(4, role="model")])
    before = copy.deepcopy(db.rows)
    transcript = "La tarjeta debe decir: Gracias, mamá. Entrega mañana."

    db.module.update_inbound_transcript("CO.SyntheticSender", "wamid.voice", transcript)

    assert len(db.rows) == 4
    assert db.rows[1:] == before[1:]
    stored = db.rows[0]
    assert stored["content"] == transcript
    assert stored["metadata"]["source_event_id"] == "wamid.voice"
    assert stored["metadata"]["received_at"] == "2026-10-22T14:44:29Z"
    assert stored["metadata"]["original_custom_field"] == "preserved"
    assert stored["metadata"]["job_id"] == "transcribing-job"
    assert stored["metadata"]["input_type"] == "audio"
    assert stored["metadata"]["is_transcription"] is True
    assert stored["metadata"]["source"] == "incoming"
    assert stored["metadata"]["deployment_sha"] == "synthetic-sha"
    update_filter = db.queries[-1][0]
    assert update_filter == {"id": 1, "phone_number": "CO.SyntheticSender", "role": "user", "metadata->>source_event_id": "wamid.voice"}
    assert "[Transcripción automática de nota de voz] " + transcript in format_handoff_history(db.rows[:1])


def test_repeated_transcription_does_not_add_a_second_user_message(transcript_database):
    db = transcript_database
    db.rows.append(incoming(1))
    for _ in range(2):
        db.module.update_inbound_transcript("CO.SyntheticSender", "wamid.voice", "Instrucción completa")
    assert len(db.rows) == 1
    assert db.rows[0]["content"] == "Instrucción completa"
    assert db.rows[0]["metadata"]["received_at"] == "2026-10-22T14:44:29Z"


def test_missing_event_cannot_create_or_rewrite_an_unrelated_message(transcript_database):
    db = transcript_database
    db.rows.append(incoming(1))
    before = copy.deepcopy(db.rows)
    db.module.update_inbound_transcript("CO.SyntheticSender", "", "No event")
    assert db.queries == []
    db.module.update_inbound_transcript("CO.SyntheticSender", "wamid.missing", "Unknown event")
    assert db.rows == before
    assert all(values is None for filters, values in db.queries)
