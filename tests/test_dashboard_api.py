import os

for key in ("SUPABASE_URL", "SUPABASE_KEY", "WA_VERIFY_TOKEN", "WA_TOKEN", "WA_PHONE_NUMBER_ID", "GEMINI_API_KEY"):
    os.environ.setdefault(key, "test-value")
os.environ.setdefault("BUSINESS_ID", "client_1")
os.environ.setdefault("DASHBOARD_API_KEY", "dashboard-secret")

from fastapi import FastAPI
from fastapi.testclient import TestClient

import dashboard_api as api


class FakeGemini:
    def __init__(self, answer):
        self.answer = answer

    def generate(self, prompt, *, json_schema=None, system_instruction=None):
        return self.answer


class FakeGitHub:
    def __init__(self):
        self.updated = None

    def get_file(self, path):
        import base64
        return {"sha": "old-sha", "content": base64.b64encode(b"current system instruction").decode("ascii")}

    def update_file(self, path, content, sha, message):
        self.updated = (path, content, sha, message)
        return {"commit": {"sha": "new-sha", "html_url": "https://github.test/commit/new-sha"}}

    def history(self, path, page, per_page):
        return [{"sha": "abc", "commit": {"author": {"date": "2026-08-04T00:00:00Z"}, "message": "Update"}}]


class FakeStorage:
    def __init__(self):
        self.uploaded = None

    def upload_pdf(self, client_name, file_obj, size_bytes):
        self.uploaded = (client_name, file_obj.read(), size_bytes)
        return {
            "public_url": f"https://storage.test/catalogos/{client_name}.pdf",
            "updated_at": "2026-08-04T00:00:00+00:00",
            "size_bytes": size_bytes,
        }

    def metadata(self, client_name):
        return {
            "public_url": f"https://storage.test/catalogos/{client_name}.pdf",
            "updated_at": "Tue, 04 Aug 2026 00:00:00 GMT",
            "size_bytes": 123,
        }


def make_client(gemini=None, github=None, storage=None):
    app = FastAPI()
    app.include_router(api.router)
    if gemini:
        app.dependency_overrides[api.get_gemini] = lambda: gemini
    if github:
        app.dependency_overrides[api.get_github] = lambda: github
    if storage:
        app.dependency_overrides[api.get_catalog_storage] = lambda: storage
    return TestClient(app), {"X-Dashboard-API-Key": "dashboard-secret"}


def test_successful_endpoints_and_catalog_path():
    github = FakeGitHub()
    storage = FakeStorage()
    client, headers = make_client(FakeGemini('[{"id":"chg-1","explicacion":"e","texto_original":"old","texto_nuevo":"new"}]'), github, storage)
    response = client.post("/api/generate-si-changes", headers=headers,
                           json={"current_si": "the old text", "user_request": "change it"})
    assert response.status_code == 200
    assert response.json() == [{"id": "chg-1", "explicacion": "e", "texto_original": "old", "texto_nuevo": "new"}]

    client, headers = make_client(FakeGemini("formatted"), github, storage)
    response = client.post("/api/format-and-save-si", headers=headers,
                           json={"client_name": "client_1", "draft_si": "draft"})
    assert response.json()["commit_sha"] == "new-sha"

    response = client.post("/api/format-and-save-si?client_name=client_1", headers=headers,
                           json={"draft_si": "draft"})
    assert response.status_code == 200

    response = client.post("/api/format-and-save-si?client_name=client_1", headers=headers,
                           json={"new_si": "draft"})
    assert response.status_code == 200

    response = client.post("/api/format-and-save-si?client_name=client_1", headers=headers,
                           json={"new_si": "draft", "commit_message": "Asistente IA: 1 cambio aplicado"})
    assert response.status_code == 200

    response = client.get("/api/current-si?client_name=client_1", headers=headers)
    assert response.json() == {"system_instruction": "current system instruction"}

    response = client.get("/api/si-history?client_name=client_1", headers=headers)
    assert response.json() == [{"date": "2026-08-04T00:00:00Z", "message": "Update", "sha": "abc"}]

    response = client.get("/api/current-catalog?client_name=client_1", headers=headers)
    assert response.json() == {
        "public_url": "https://storage.test/catalogos/client_1.pdf",
        "updated_at": "Tue, 04 Aug 2026 00:00:00 GMT",
        "size_bytes": 123,
    }

    response = client.post("/api/upload-catalog?client_name=client_1", headers=headers,
                           files={"file": ("ignored.pdf", b"%PDF-1.7\nbody", "application/pdf")})
    assert response.status_code == 200
    assert response.json()["public_url"] == "https://storage.test/catalogos/client_1.pdf"
    assert storage.uploaded == ("client_1", b"%PDF-1.7\nbody", 13)


def test_invalid_gemini_json_and_original_validation():
    client, headers = make_client(FakeGemini("not json"))
    payload = {"current_si": "one", "user_request": "change"}
    assert client.post("/api/generate-si-changes", headers=headers, json=payload).status_code == 502
    client, headers = make_client(FakeGemini('[{"id":"chg-1","explicacion":"e","texto_original":"missing","texto_nuevo":"x"}]'))
    assert client.post("/api/generate-si-changes", headers=headers, json=payload).status_code == 422
    client, headers = make_client(FakeGemini('[{"id":"chg-1","explicacion":"e","texto_original":"one","texto_nuevo":"x"}]'))
    assert client.post("/api/generate-si-changes", headers=headers,
                       json={"current_si": "one one", "user_request": "change"}).status_code == 422


def test_auth_traversal_and_bad_pdfs():
    client, headers = make_client(FakeGemini("x"), FakeGitHub(), FakeStorage())
    assert client.get("/api/current-si?client_name=ok").status_code == 401
    assert client.get("/api/current-si?client_name=../secret", headers=headers).status_code == 422
    assert client.get("/api/si-history?client_name=../secret", headers=headers).status_code == 422
    assert client.post("/api/upload-catalog?client_name=client_1", headers=headers,
                       files={"file": ("x.txt", b"hello", "text/plain")}).status_code == 400
    assert client.post("/api/upload-catalog?client_name=client_1", headers=headers,
                       files={"file": ("x.pdf", b"", "application/pdf")}).status_code == 400


def test_sha_conflict_is_sanitized(monkeypatch):
    class Response:
        status_code = 409
        text = "token=very-secret catalog-sensitive"

    adapter = object.__new__(api.GitHubAdapter)
    try:
        adapter._check(Response())
        assert False
    except Exception as exc:
        assert exc.status_code == 409
        assert "very-secret" not in exc.detail


def test_oversized_pdf(monkeypatch):
    monkeypatch.setattr(api.config, "DASHBOARD_MAX_CATALOG_MB", 0)
    client, headers = make_client(FakeGemini("x"), FakeGitHub(), FakeStorage())
    response = client.post("/api/upload-catalog?client_name=client_1", headers=headers,
                           files={"file": ("x.pdf", b"%PDF-123", "application/pdf")})
    assert response.status_code == 413


def test_gemini_provider_error_includes_real_message():
    class RejectingGemini:
        def generate(self, prompt, *, json_schema=None, system_instruction=None):
            raise api.GeminiProviderError(400, "INVALID_ARGUMENT", "response_schema.items.properties[text] is invalid")

    client, headers = make_client(RejectingGemini())
    response = client.post("/api/generate-si-changes", headers=headers,
                           json={"current_si": "literal", "user_request": "change"})

    assert response.status_code == 422
    assert response.json() == {
        "detail": "Gemini rechazó la solicitud: response_schema.items.properties[text] is invalid"
    }
    assert "Gemini no pudo procesar la solicitud" not in response.text


def test_gemini_retries_fallback_model_on_not_found(monkeypatch):
    from google.genai.errors import APIError

    calls = []

    class FakeModels:
        def generate_content(self, *, model, contents, config):
            calls.append(model)
            if model == "old-model":
                raise APIError(404, {"error": {"code": 404, "status": "NOT_FOUND", "message": "old model unavailable"}})
            return type("Response", (), {"text": "ok"})()

    adapter = object.__new__(api.GeminiAdapter)
    adapter.client = type("Client", (), {"models": FakeModels()})()
    monkeypatch.setattr(api.config, "GEMINI_DASHBOARD_MODELS", ["old-model", "new-model"])

    assert adapter.generate("prompt") == "ok"
    assert calls == ["old-model", "new-model"]


def test_format_and_save_requires_client_without_echoing_draft():
    client, headers = make_client(FakeGemini("formatted"), FakeGitHub())
    response = client.post("/api/format-and-save-si", headers=headers, json={"draft_si": "sensitive prompt body"})

    assert response.status_code == 422
    assert response.json() == {"detail": "client_name requerido"}
    assert "sensitive prompt body" not in response.text


def test_format_and_save_accepts_system_instruction_alias():
    client, headers = make_client(FakeGemini("formatted"), FakeGitHub())
    response = client.post("/api/format-and-save-si?client_name=client_1", headers=headers,
                           json={"system_instruction": "draft"})

    assert response.status_code == 200


def test_format_and_save_ignores_lovable_metadata_without_echoing_prompt():
    client, headers = make_client(FakeGemini("formatted"), FakeGitHub())
    response = client.post("/api/format-and-save-si?client_name=client_1", headers=headers,
                           json={"new_si": "sensitive prompt body", "commit_message": "Asistente IA"})

    assert response.status_code == 200
    assert "sensitive prompt body" not in response.text


def test_catalog_storage_maps_provider_entity_too_large_to_413():
    class Response:
        status_code = 400
        text = '{"statusCode":"413","error":"Payload too large","code":"EntityTooLarge"}'

    adapter = object.__new__(api.CatalogStorageAdapter)
    try:
        adapter.check_upload_response(Response(), "tanaka", 65036357)
        assert False
    except Exception as exc:
        assert exc.status_code == 413
        assert "Payload too large" in exc.detail


def test_catalog_storage_uses_resumable_endpoint_and_direct_storage_hostname():
    adapter = object.__new__(api.CatalogStorageAdapter)
    adapter.base_url = "https://tbvcvqddpppqlwuehdaf.supabase.co"
    adapter.bucket = "catalogos"
    assert adapter.storage_hostname() == "https://tbvcvqddpppqlwuehdaf.storage.supabase.co"


def test_catalog_storage_maps_already_exists_to_409():
    class Response:
        status_code = 409
        text = "The resource already exists"

    adapter = object.__new__(api.CatalogStorageAdapter)
    try:
        adapter.check_upload_response(Response(), "tanaka", 10)
        assert False
    except Exception as exc:
        assert exc.status_code == 409
        assert "already exists" in exc.detail
