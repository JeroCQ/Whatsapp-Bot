import os

import pytest

for key in ("SUPABASE_KEY", "WA_VERIFY_TOKEN", "WA_TOKEN", "WA_PHONE_NUMBER_ID", "GEMINI_API_KEY"):
    os.environ.setdefault(key, "test-value")
os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("BUSINESS_ID", "client_1")
os.environ.setdefault("DASHBOARD_API_KEY", "dashboard-secret")

from fastapi import FastAPI, HTTPException
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

    def health(self, path):
        return {"status": 200, "scopes": "repo", "accepted_permissions": "contents=write", "ratelimit_remaining": "99"}


class FakeStorage:
    def __init__(self):
        self.uploaded = None

    def upload(self, client_name, file_obj, size_bytes, extension, content_type):
        self.uploaded = (client_name, file_obj.read(), size_bytes, extension, content_type)
        return {
            "publicUrl": f"https://storage.test/catalogos/{client_name}.{extension}",
            "updatedAt": "2026-08-04T00:00:00+00:00",
            "sizeBytes": size_bytes,
            "contentType": content_type,
            "filename": f"{client_name}.{extension}",
        }

    def metadata(self, client_name):
        return {
            "publicUrl": f"https://storage.test/catalogos/{client_name}.pdf",
            "updatedAt": "Tue, 04 Aug 2026 00:00:00 GMT",
            "sizeBytes": 123,
            "contentType": "application/pdf",
            "filename": f"{client_name}.pdf",
        }

    def health(self):
        return None


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
    assert response.json() == {
        "system_instruction": "current system instruction",
        "client_name": "client_1",
        "path": "src/clients/client_1/system_instruction.txt",
    }

    response = client.get("/api/si-history?client_name=client_1", headers=headers)
    assert response.json() == [{"date": "2026-08-04T00:00:00Z", "message": "Update", "sha": "abc"}]

    response = client.get("/api/dashboard-health", headers=headers)
    health = response.json()
    assert health["github"]["ok"] is True
    assert health["github"]["status"] == 200
    assert health["supabase_storage"]["ok"] is True
    assert health["supabase_storage"]["object"] == "client_1.pdf"
    assert health["configuration"]["business_id"] == "client_1"
    assert health["configuration"]["system_instruction_path"] == "src/clients/client_1/system_instruction.txt"

    assert health["gemini"]["ok"] is True
    assert "GITHUB_TOKEN" in health["configuration"]["present"]

    response = client.get("/api/current-catalog?client_name=client_1", headers=headers)
    assert response.json() == {
        "publicUrl": "https://storage.test/catalogos/client_1.pdf",
        "updatedAt": "Tue, 04 Aug 2026 00:00:00 GMT",
        "sizeBytes": 123,
        "contentType": "application/pdf",
        "filename": "client_1.pdf",
    }


def test_explicit_si_path_cannot_cross_business(monkeypatch):
    monkeypatch.setattr(api.config, "GITHUB_SI_PATH", "src/clients/memos/system_instruction.txt")

    with pytest.raises(ValueError, match="no corresponde a BUSINESS_ID=client_1"):
        api.system_instruction_path("client_1")


def test_explicit_si_path_may_match_deployment_business(monkeypatch):
    monkeypatch.setattr(api.config, "GITHUB_SI_PATH", "src/clients/client_1/system_instruction.txt")
    storage = FakeStorage()
    client, headers = make_client(storage=storage)

    assert api.system_instruction_path("client_1") == "src/clients/client_1/system_instruction.txt"

    response = client.post("/api/upload-catalog?client_name=client_1", headers=headers,
                           files={"file": ("ignored.pdf", b"%PDF-1.7\nbody", "application/pdf")})
    assert response.status_code == 200
    assert response.json()["publicUrl"] == "https://storage.test/catalogos/client_1.pdf"
    assert storage.uploaded == ("client_1", b"%PDF-1.7\nbody", 13, "pdf", "application/pdf")

    png = b"\x89PNG\r\n\x1a\n" + b"image-body"
    response = client.post("/api/upload-catalog?client_name=client_1", headers=headers,
                           files={"file": ("catalog.png", png, "image/png")})
    assert response.status_code == 200
    assert response.json()["contentType"] == "image/png"
    assert response.json()["filename"] == "client_1.png"
    assert storage.uploaded == ("client_1", png, len(png), "png", "image/png")


def test_si_path_normalizes_railway_quotes_whitespace_and_separators(monkeypatch):
    monkeypatch.setattr(
        api.config,
        "GITHUB_SI_PATH",
        '  "/src\\clients\\client_1\\system_instruction.txt"\r\n',
    )

    assert api.system_instruction_path("client_1") == "src/clients/client_1/system_instruction.txt"


def test_si_path_mismatch_reports_masked_value_and_branch(monkeypatch):
    raw = '"src/clients/tanaka/system_instruction.txt"'
    monkeypatch.setattr(api.config, "GITHUB_SI_PATH", raw)
    monkeypatch.setattr(api.config, "GITHUB_BRANCH", "production")

    with pytest.raises(ValueError) as error:
        api.system_instruction_path("client_1")

    message = str(error.value)
    assert "rama='production'" in message
    assert "recibido=" in message
    assert raw not in message
    assert "src/clients/client_1/system_instruction.txt" in message


def test_dashboard_startup_logs_raw_si_path_with_repr(monkeypatch, caplog):
    monkeypatch.setattr(api.config, "GITHUB_SI_PATH", '"src/clients/memos/system_instruction.txt"\r\n')
    monkeypatch.setattr(api.config, "GITHUB_BRANCH", "main")

    with caplog.at_level("INFO", logger="dashboard_api"):
        api.log_dashboard_startup_configuration()

    assert "GITHUB_SI_PATH raw='\"src/clients/memos/system_instruction.txt\"\\r\\n'" in caplog.text
    assert "branch='main'" in caplog.text


def test_memos_system_instruction_exists_at_exact_validated_path():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    assert (root / "src/clients/memos/system_instruction.txt").is_file()


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
    error = client.post("/api/upload-catalog?client_name=client_1", headers=headers,
                        files={"file": ("x.txt", b"hello", "text/plain")})
    assert "PDF, JPG/JPEG, PNG y WebP" in error.json()["detail"]
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


def test_github_errors_are_classified_and_logged(caplog):
    class Response:
        def __init__(self, status, headers=None):
            self.status_code = status
            self.headers = headers or {}
            self.text = '{"message":"provider detail"}'

        def json(self):
            return {"message": "provider detail"}

    adapter = object.__new__(api.GitHubAdapter)
    cases = [
        (Response(401), "inválido o está vencido"),
        (Response(403, {"x-ratelimit-remaining": "0", "x-ratelimit-reset": "123"}), "límite de solicitudes"),
        (Response(403, {"x-accepted-github-permissions": "contents=read"}), "Contents: Read and write"),
        (Response(404), "GITHUB_OWNER/GITHUB_REPO/GITHUB_BRANCH/GITHUB_SI_PATH"),
    ]
    for response, expected in cases:
        with pytest.raises(HTTPException) as caught:
            adapter._check(response)
        assert expected in caught.value.detail
    assert "body={\"message\":\"provider detail\"}" in caplog.text
    assert "x-ratelimit-remaining=0" in caplog.text
    assert "x-ratelimit-reset=123" in caplog.text
    assert "x-accepted-github-permissions=contents=read" in caplog.text


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


def test_catalog_storage_explains_cross_project_service_role():
    class Response:
        status_code = 403
        text = '{"message":"Invalid JWT"}'

    adapter = object.__new__(api.CatalogStorageAdapter)
    adapter.bucket = "catalogos"
    with pytest.raises(HTTPException) as error:
        adapter.check_upload_response(Response(), "tanaka", 10)

    assert error.value.status_code == 502
    assert "SUPABASE_SERVICE_ROLE_KEY" in error.value.detail
    assert "SUPABASE_URL" in error.value.detail


def test_catalog_storage_explains_missing_bucket():
    class Response:
        status_code = 404
        text = '{"message":"Bucket not found"}'

    adapter = object.__new__(api.CatalogStorageAdapter)
    adapter.bucket = "catalogos"
    with pytest.raises(HTTPException) as error:
        adapter.check_upload_response(Response(), "tanaka", 10)

    assert error.value.status_code == 502
    assert "catalogos" in error.value.detail


def test_catalog_storage_maps_tus_creation_transport_error(monkeypatch):
    adapter = object.__new__(api.CatalogStorageAdapter)
    adapter.base_url = "https://project.supabase.co"
    adapter.bucket = "catalogos"
    adapter.headers = {"Authorization": "Bearer secret", "apikey": "secret"}
    monkeypatch.setattr(api.requests, "post", lambda *args, **kwargs: (_ for _ in ()).throw(api.requests.ConnectionError()))

    with pytest.raises(HTTPException) as error:
        adapter.create_tus_upload("tanaka", 10, "pdf", "application/pdf")

    assert error.value.status_code == 502
    assert "iniciar la carga" in error.value.detail


def test_catalog_delete_treats_embedded_no_such_key_as_absent(monkeypatch):
    class Response:
        status_code = 400
        text = '{"statusCode":"404","message":"Object not found","code":"NoSuchKey"}'

    adapter = object.__new__(api.CatalogStorageAdapter)
    adapter.base_url = "https://project.supabase.co"
    adapter.bucket = "catalogos"
    adapter.headers = {"Authorization": "Bearer secret", "apikey": "secret"}
    monkeypatch.setattr(api.requests, "delete", lambda *args, **kwargs: Response())

    assert adapter.delete_existing("tanaka", "pdf") is None


def test_catalog_upload_retries_after_conflict_and_absent_delete(monkeypatch):
    from io import BytesIO

    adapter = object.__new__(api.CatalogStorageAdapter)
    calls = []
    deleted = []

    def upload_once(*args):
        calls.append(args)
        if len(calls) == 1:
            raise HTTPException(409, "already exists")
        return {"filename": "tanaka.pdf"}

    monkeypatch.setattr(adapter, "upload_once", upload_once)
    monkeypatch.setattr(adapter, "delete_existing", lambda client, extension="pdf": deleted.append((client, extension)))

    result = adapter.upload("tanaka", BytesIO(b"%PDF-test"), 9, "pdf", "application/pdf")

    assert result == {"filename": "tanaka.pdf"}
    assert len(calls) == 2
    assert deleted[0] == ("tanaka", "pdf")


def test_catalog_tus_metadata_preserves_image_content_type(monkeypatch):
    class Response:
        status_code = 201
        text = ""
        headers = {"location": "/storage/v1/upload/resumable/upload-id"}

    captured = {}
    monkeypatch.setattr(api.requests, "post", lambda url, **kwargs: captured.update(url=url, **kwargs) or Response())
    adapter = object.__new__(api.CatalogStorageAdapter)
    adapter.base_url = "https://project.supabase.co"
    adapter.bucket = "catalogos"
    adapter.headers = {"Authorization": "Bearer secret", "apikey": "secret"}

    adapter.create_tus_upload("client_1", 123, "png", "image/png")
    metadata = captured["headers"]["Upload-Metadata"]
    decoded = {
        item.split(" ", 1)[0]: __import__("base64").b64decode(item.split(" ", 1)[1]).decode()
        for item in metadata.split(",")
    }
    assert decoded == {
        "bucketName": "catalogos",
        "objectName": "client_1.png",
        "contentType": "image/png",
        "cacheControl": "300",
    }
    assert captured["headers"]["x-upsert"] == "true"


def test_catalog_upload_removes_other_client_formats(monkeypatch):
    adapter = object.__new__(api.CatalogStorageAdapter)
    deleted = []
    monkeypatch.setattr(adapter, "upload_once", lambda *args: {"filename": "client_1.png"})
    monkeypatch.setattr(adapter, "delete_existing", lambda client, extension="pdf": deleted.append((client, extension)))

    assert adapter.upload("client_1", object(), 10, "png", "image/png") == {"filename": "client_1.png"}
    assert {extension for _, extension in deleted} == {"pdf", "jpg", "jpeg", "webp"}


def test_catalog_metadata_finds_image_after_missing_pdf(monkeypatch):
    class Response:
        def __init__(self, status_code, body=None):
            self.status_code = status_code
            self._body = body or {"message": "Object not found"}
            self.text = __import__("json").dumps(self._body)

        def json(self):
            return self._body

    monkeypatch.setattr(
        api.requests,
        "get",
        lambda url, **kwargs: Response(200, {
            "metadata": {"mimetype": "image/png", "size": 321},
            "updated_at": "now",
        }) if url.endswith(".png") else Response(404),
    )
    adapter = object.__new__(api.CatalogStorageAdapter)
    adapter.base_url = "https://project.supabase.co"
    adapter.bucket = "catalogos"
    adapter.headers = {"Authorization": "Bearer secret", "apikey": "secret"}

    assert adapter.metadata("client_1") == {
        "publicUrl": "https://project.supabase.co/storage/v1/object/public/catalogos/client_1.png",
        "updatedAt": "now",
        "sizeBytes": 321,
        "contentType": "image/png",
        "filename": "client_1.png",
    }


def test_catalog_metadata_returns_explicit_404_when_all_objects_are_absent(monkeypatch):
    class Response:
        status_code = 404
        text = '{"message":"Object not found"}'

        def json(self):
            return {"message": "Object not found"}

    monkeypatch.setattr(api.requests, "get", lambda *args, **kwargs: Response())
    adapter = object.__new__(api.CatalogStorageAdapter)
    adapter.base_url = "https://project.supabase.co"
    adapter.bucket = "catalogos"
    adapter.headers = {"Authorization": "Bearer secret", "apikey": "secret"}

    with pytest.raises(HTTPException) as error:
        adapter.metadata("memos")

    assert error.value.status_code == 404
    assert error.value.detail == {
        "error": "catalog_not_found",
        "message": "El catálogo todavía no existe en el almacenamiento",
        "provider_status": 404,
        "bucket": "catalogos",
        "path": "memos.{pdf|jpg|jpeg|png|webp}",
    }


def test_catalog_metadata_propagates_provider_status_message_bucket_and_path(monkeypatch, caplog):
    class Response:
        status_code = 403
        text = '{"message":"Invalid JWT for project"}'

        def json(self):
            return {"message": "Invalid JWT for project"}

    monkeypatch.setattr(api.requests, "get", lambda *args, **kwargs: Response())
    adapter = object.__new__(api.CatalogStorageAdapter)
    adapter.base_url = "https://project.supabase.co"
    adapter.bucket = "catalogos"
    adapter.headers = {"Authorization": "Bearer secret", "apikey": "secret"}

    with caplog.at_level("ERROR", logger="dashboard_api"), pytest.raises(HTTPException) as error:
        adapter.metadata("memos")

    assert error.value.status_code == 502
    assert error.value.detail == {
        "error": "storage_provider_error",
        "message": "Invalid JWT for project",
        "provider_status": 403,
        "bucket": "catalogos",
        "path": "memos.pdf",
    }
    assert "status=403" in caplog.text
    assert "message=Invalid JWT for project" in caplog.text
    assert "bucket=catalogos" in caplog.text
    assert "path=memos.pdf" in caplog.text


def test_catalog_metadata_never_echoes_service_role(monkeypatch):
    secret = "service-role-sensitive"

    class Response:
        status_code = 403
        text = '{"message":"credential service-role-sensitive rejected"}'

        def json(self):
            return {"message": "credential service-role-sensitive rejected"}

    monkeypatch.setattr(api.config, "SUPABASE_KEY", secret)
    monkeypatch.setattr(api.requests, "get", lambda *args, **kwargs: Response())
    adapter = object.__new__(api.CatalogStorageAdapter)
    adapter.base_url = "https://project.supabase.co"
    adapter.bucket = "catalogos"
    adapter.headers = {"Authorization": f"Bearer {secret}", "apikey": secret}

    with pytest.raises(HTTPException) as error:
        adapter.metadata("memos")

    assert secret not in str(error.value.detail)
    assert "[REDACTED]" in error.value.detail["message"]


def test_current_catalog_route_returns_explicit_not_found_json(monkeypatch):
    class Response:
        status_code = 404
        text = '{"message":"Object not found"}'

        def json(self):
            return {"message": "Object not found"}

    monkeypatch.setattr(api.requests, "get", lambda *args, **kwargs: Response())
    storage = api.CatalogStorageAdapter()
    client, headers = make_client(storage=storage)

    response = client.get("/api/current-catalog?client_name=client_1", headers=headers)

    assert response.status_code == 404
    assert response.json()["detail"]["error"] == "catalog_not_found"
    assert response.json()["detail"]["bucket"] == "catalogos"
    assert response.json()["detail"]["path"] == "client_1.{pdf|jpg|jpeg|png|webp}"
