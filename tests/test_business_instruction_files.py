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


def test_tanaka_instruction_answers_before_using_catalog_and_limits_follow_up():
    instruction = (ROOT / "src/clients/tanaka/system_instruction.txt").read_text(encoding="utf-8")
    assert "El catálogo apoya la respuesta, pero no la reemplaza" in instruction
    assert 'escribe solamente "precio"' in instruction
    assert "ya eligió al menos un producto" in instruction
    assert "envío de catálogo sin selección de producto" in instruction
    assert "Pregunta repetida o corrección" in instruction
    assert "súper pendiente" in instruction
    assert "Si el cliente no responde a ese seguimiento, no generes otro" in instruction
    assert "entre las 8:00 a.m. y las 6:00 p.m." in instruction


def test_tanaka_instruction_uses_confirmed_product_facts():
    instruction = (ROOT / "src/clients/tanaka/system_instruction.txt").read_text(encoding="utf-8")
    assert "40 días" not in instruction
    assert instruction.count("45 días") == 2
    assert instruction.count("avena certificada sin gluten") == 2
    assert "Los combos se consideran disponibles bajo la misma regla del catálogo" in instruction


def test_tanaka_instruction_defines_current_customer_and_shipping_rules():
    instruction = (ROOT / "src/clients/tanaka/system_instruction.txt").read_text(encoding="utf-8")
    assert "Año de nacimiento" in instruction
    assert "Mes de cumpleaños" not in instruction
    assert "Solo para estos destinos también puede pagar contraentrega la totalidad del pedido" in instruction
    assert "entre $20.000 y $30.000 por kilo" in instruction
    assert "nevera térmica de $20.000" in instruction
    assert "ÚNICAMENTE a Medellín, Bogotá y Barranquilla" in instruction
    assert "$13.850 por un litro volumétrico a Medellín y Bogotá" in instruction
    assert "$15.350 por litro volumétrico a Barranquilla" in instruction
    assert "@tanakasaludable" in instruction
    assert "etiquetarnos cuando recibas o pruebes tus productos" in instruction


def test_tanaka_instruction_preserves_purchase_momentum_and_recognizes_returning_customers():
    instruction = (ROOT / "src/clients/tanaka/system_instruction.txt").read_text(encoding="utf-8")
    assert "Qué bueno tenerte de nuevo" in instruction
    assert "está guardado su nombre" in instruction
    assert "El momentum de compra es sagrado" in instruction
    assert "ubicación general mínima necesaria" in instruction
    assert "NO pidas todavía dirección exacta" in instruction
    assert "facturación electrónica son completamente opcionales" in instruction
    assert "https://www.instagram.com/tanakasaludable/?hl=es" in instruction
    assert "al pie de ese mismo mensaje" in instruction


def test_tanaka_instruction_includes_official_business_contact_and_dispatch_details():
    instruction = (ROOT / "src/clients/tanaka/system_instruction.txt").read_text(encoding="utf-8")
    assert "Nombre legal:** Tanaka Saludable SAS" in instruction
    assert "Celular:** 3025991292" in instruction
    assert "Correo electrónico:** tanakasaludablecali@gmail.com" in instruction
    assert "Manager:** María Camila" in instruction
    assert "salen únicamente los lunes o martes" in instruction
    assert "Tardan aproximadamente dos días en llegar" in instruction
    assert "lunes a miércoles" not in instruction
