"""Private database connections for the central migration tools."""

import os

import psycopg
from psycopg.conninfo import conninfo_to_dict

CENTRAL_REF = "tbvcvqddpppqlwuehdaf"


class CopyError(RuntimeError):
    pass


def parse_dsn(raw):
    return raw.replace("postgresql+psycopg://", "postgresql://", 1)


def matches_project(settings, project_ref):
    host, user = settings.get("host", ""), settings.get("user", "")
    return host == f"db.{project_ref}.supabase.co" or (
        host.endswith(".pooler.supabase.com") and user.endswith("." + project_ref))


def check_project(raw, project_ref):
    settings = conninfo_to_dict(parse_dsn(raw))
    if not matches_project(settings, project_ref):
        raise CopyError("La conexión no pertenece al proyecto Supabase esperado.")
    if settings.get("hostaddr") or settings.get("service"):
        raise CopyError("La conexión debe poder atribuirse al host Supabase esperado.")
    if str(settings.get("port", "5432")) == "6543":
        raise CopyError("La copia usa conexión directa o pooler de sesión, con transacciones y COPY.")


def is_central_project(raw):
    return matches_project(conninfo_to_dict(parse_dsn(raw)), CENTRAL_REF)


def check_central_project(raw):
    check_project(raw, CENTRAL_REF)


def connect(env_name, *, central=False):
    raw = os.environ.get(env_name, "")
    if not raw:
        raise CopyError(f"Falta una referencia de conexión privada: {env_name}")
    if central:
        check_central_project(raw)
    db = psycopg.connect(parse_dsn(raw), connect_timeout=5, application_name="chatty_central_copy")
    db.autocommit = True
    return db
