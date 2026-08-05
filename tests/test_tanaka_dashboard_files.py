import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _literal_system_instruction() -> str:
    tree = ast.parse((ROOT / "bot.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == "SYSTEM_INSTRUCTION" for target in node.targets):
            return ast.literal_eval(node.value).strip()
    raise AssertionError("SYSTEM_INSTRUCTION literal not found")


def test_tanaka_dashboard_instruction_starts_with_current_prompt():
    stored = (ROOT / "src/clients/tanaka/system_instruction.txt").read_text(encoding="utf-8").strip()
    assert stored == _literal_system_instruction()


def test_tanaka_catalog_placeholder_was_removed_from_repo_storage():
    catalog = ROOT / "public/catalogos/tanaka_catalogo.pdf"
    assert not catalog.exists()
