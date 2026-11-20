import pytest
from fastapi import HTTPException

from crm_service.bot_management import authorize_management, management_path_allowed
from crm_service.security import Actor


def test_shared_inbox_does_not_expand_management_permissions():
    actor = Actor("shared", ("tanaka", "velvet"), login_profile="tanaka")
    authorize_management(actor, "tanaka")
    with pytest.raises(HTTPException) as error:
        authorize_management(actor, "velvet")
    assert error.value.status_code == 403


def test_wholesale_uses_the_same_memos_bot_but_not_other_brands():
    actor = Actor("wholesale", ("memos", "tanaka"), login_profile="memos_mayoristas")
    authorize_management(actor, "memos")
    with pytest.raises(HTTPException):
        authorize_management(actor, "tanaka")


def test_admin_still_requires_registered_business_scope():
    actor = Actor("admin", ("briosos",), is_admin=True, login_profile="admin")
    authorize_management(actor, "briosos")
    with pytest.raises(HTTPException):
        authorize_management(actor, "unregistered")


@pytest.mark.parametrize("path,method", [
    ("api/current-si", "GET"), ("api/format-and-save-si", "POST"),
    ("api/catalogs/catalogo_demo/file", "POST"),
    ("api/catalogs/catalogo_demo", "DELETE"),
    ("api/si-job/12345678-1234-1234-1234-123456789abc", "GET"),
])
def test_existing_management_contract_is_allowlisted(path, method):
    assert management_path_allowed(path, method)


@pytest.mark.parametrize("path,method", [
    ("internal/runtime/bootstrap", "GET"), ("api/current-si", "DELETE"),
    ("api/catalogs/catalogo_demo/../../control", "POST"),
    ("https://other.example/api/catalogs", "GET"),
])
def test_no_arbitrary_proxy_or_destructive_read_route(path, method):
    assert not management_path_allowed(path, method)
