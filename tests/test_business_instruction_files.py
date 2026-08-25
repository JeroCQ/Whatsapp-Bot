from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_memos_instruction_is_available_to_the_selected_deployment():
    instruction = (ROOT / "src/clients/memos/system_instruction.txt").read_text(encoding="utf-8")
    assert "Quesos Memo's" in instruction
    assert "829-0002441-2" in instruction
    assert "follow_up_delay_minutes = 120" in instruction


def test_tanaka_instruction_limits_same_day_delivery_by_local_time():
    instruction = (ROOT / "src/clients/tanaka/system_instruction.txt").read_text(encoding="utf-8")
    assert "fecha y hora local de Colombia" in instruction
    assert "lunes a viernes antes de las 5:00 p.m." in instruction
    assert "no garantices una hora de llegada" in instruction
    assert "productos incluidos en el catálogo están disponibles" in instruction
