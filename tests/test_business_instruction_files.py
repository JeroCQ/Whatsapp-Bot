from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_memos_instruction_is_available_to_the_selected_deployment():
    instruction = (ROOT / "src/clients/memos/system_instruction.txt").read_text(encoding="utf-8")
    assert "Quesos Memo's" in instruction
    assert "829-0002441-2" in instruction
    assert "follow_up_delay_minutes = 120" in instruction
