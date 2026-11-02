import database


class Query:
    def __init__(self, table, state):
        self.table = table
        self.state = state
        self.filters = {}
        self.data = None
    def select(self, *a): return self
    def update(self, values): self.values = values; return self
    def eq(self, key, value): self.filters[key] = value; return self
    def execute(self):
        if not hasattr(self, "values"):
            self.data = [{"phone_number": "57300"}] if self.filters.get("chatwoot_conversation_id") == 100 else []
        elif self.state["conversation"] == self.filters.get("chatwoot_conversation_id"):
            self.state.update(conversation=None, paused=False)
            self.data = [{"phone_number": "57300"}]
        else:
            self.data = []
        return self


class FakeSupabase:
    def __init__(self, state): self.state = state
    def table(self, name): return Query(name, self.state)


def test_stale_resolution_cannot_clear_newer_conversation(monkeypatch):
    state = {"conversation": 200, "paused": True}
    monkeypatch.setattr(database, "supabase", FakeSupabase(state))
    # Lookup observes old A, but the conditional update sees newer B.
    assert database.resume_bot_state(100) is None
    assert state == {"conversation": 200, "paused": True}
