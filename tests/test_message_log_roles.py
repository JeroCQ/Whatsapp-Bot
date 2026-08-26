"""Static contract checks between message-log writers, readers, and Supabase."""

import ast
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_ROLES = {"user", "model", "system", "asesor"}
MIGRATION = ROOT / "supabase" / "migrations" / "20260826000000_message_log_roles.sql"


def _literal_roles_passed_to_save_message_log(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    roles = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Name) or node.func.id != "save_message_log":
            continue
        assert len(node.args) >= 2, f"Missing role argument at {path}:{node.lineno}"
        role = node.args[1]
        assert isinstance(role, ast.Constant) and isinstance(role.value, str), (
            f"Message-log role must be statically auditable at {path}:{node.lineno}"
        )
        roles.add(role.value)
    return roles


def _check_roles(sql: str) -> set[str]:
    match = re.search(
        r"constraint\s+message_logs_role_check\s+check\s*\(\s*role\s+in\s*\(([^)]+)\)\s*\)",
        sql,
        flags=re.IGNORECASE | re.DOTALL,
    )
    assert match, "Named message_logs role constraint was not found"
    return set(re.findall(r"'([^']+)'", match.group(1)))


def test_persisted_and_interpreted_roles_match_bootstrap_schema():
    writer_roles = _literal_roles_passed_to_save_message_log(ROOT / "main.py")
    writer_roles |= _literal_roles_passed_to_save_message_log(ROOT / "bot.py")
    assert writer_roles == EXPECTED_ROLES

    bot_source = (ROOT / "bot.py").read_text(encoding="utf-8")
    interpreted_roles = set(
        re.findall(r"`([^`]+)`", re.search(
            r"Conserva el significado de cada `role`:(.*?)son\s+autores distintos",
            bot_source,
            flags=re.DOTALL,
        ).group(1))
    )
    assert interpreted_roles == EXPECTED_ROLES

    bootstrap = (ROOT / "supabase" / "bootstrap.sql").read_text(encoding="utf-8")
    assert _check_roles(bootstrap) == EXPECTED_ROLES


def test_existing_projects_receive_idempotent_named_constraint_migration():
    migration = MIGRATION.read_text(encoding="utf-8")
    assert re.search(
        r"drop\s+constraint\s+if\s+exists\s+message_logs_role_check",
        migration,
        flags=re.IGNORECASE,
    )
    assert _check_roles(migration) == EXPECTED_ROLES
