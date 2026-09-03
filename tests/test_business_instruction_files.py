from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_memos_instruction_is_available_to_the_selected_deployment():
    instruction = (ROOT / "src/clients/memos/system_instruction.txt").read_text(encoding="utf-8")
    assert "Quesos Memo's" in instruction
    assert "829-0002441-2" in instruction
    assert "follow_up_delay_minutes = 120" in instruction


def test_velvet_instruction_uses_confirmed_identity_catalog_and_logistics():
    instruction = (ROOT / "src/clients/velvet/system_instruction.txt").read_text(encoding="utf-8")
    assert "Camila" in instruction
    assert "Velvet Repostería y Mochis Velvet" in instruction
    assert "Caja x 6 unidades: **$28.000 COP**" in instruction
    assert "De 40 a 95 unidades: **$3.500 COP**" in instruction
    assert "Caja x 12 unidades: **$50.000 COP**" in instruction
    assert "Desde 96 unidades en adelante: **$3.000 COP**" in instruction
    assert "Carrera 10 # 47-31, Barrio El Troncal, Cali" in instruction
    assert "Lunes a sábado de 9:00 a.m. a 5:00 p.m." in instruction
    assert "dentro de la ciudad que no pertenezcan a las zonas más lejanas" in instruction
    assert "**$9.000 COP**" in instruction
    assert "follow_up_delay_minutes = 120" in instruction
    assert "trigger_handoff = true" in instruction
    assert "Soy Camila, asesora de Velvet en Cali" in instruction
    assert "Cada catálogo se envía una sola vez" in instruction


def test_velvet_catalog_messages_are_brief_visual_and_gender_neutral():
    instruction = (ROOT / "src/clients/velvet/system_instruction.txt").read_text(encoding="utf-8")
    assert "pieza visual autosuficiente" in instruction
    assert "no copies en el texto sus precios, sabores, tamaños ni rangos de cantidades" in instruction
    assert "una o dos frases cortas" in instruction
    assert "muchos”" in instruction
    assert "no autorizan repetir las escalas" in instruction
    assert "Voz y trato unisex" in instruction
    assert "evita “señor”, “señora”, “bienvenido”, “bienvenida”" in instruction
    assert "¿Se te antojan tortas o mochis?" in instruction


def test_velvet_instruction_does_not_import_unconfirmed_tanaka_commercial_facts():
    instruction = (ROOT / "src/clients/velvet/system_instruction.txt").read_text(encoding="utf-8")
    assert "Alexandra" not in instruction
    assert "Tanaka Saludable" not in instruction
    assert "51400015704" not in instruction
    assert "NIT 901888354" not in instruction
    assert "nevera térmica" not in instruction


def test_velvet_instruction_preserves_sales_flow_and_brand_identified_handoff():
    instruction = (ROOT / "src/clients/velvet/system_instruction.txt").read_text(encoding="utf-8")
    assert "Orden obligatorio antes de responder" in instruction
    assert "El catálogo apoya la respuesta, pero no la reemplaza" in instruction
    assert 'escribe solamente "precio"' in instruction
    assert "Qué gusto atenderte nuevamente" in instruction
    assert "Respuestas breves con contexto" in instruction
    assert "El momentum de compra es sagrado" in instruction
    assert "no pidas todavía la dirección exacta" in instruction
    assert "Resumen progresivo" in instruction
    assert "Datos adicionales" in instruction
    assert "entre las 8:00 a.m. y las 6:00 p.m." in instruction
    assert "no generes otro" in instruction
    assert "handoff_reason" in instruction
    assert "empiece exactamente con el identificador `VELVET: `" in instruction
    assert "deja `follow_up_message` vacío" in instruction


def test_velvet_instruction_answers_confirmed_volcano_definition_without_handoff():
    instruction = (ROOT / "src/clients/velvet/system_instruction.txt").read_text(encoding="utf-8")
    assert "exterior de bizcocho cocido y un interior líquido" in instruction
    assert "no actives handoff solo porque el cliente pregunte qué es" in instruction


def test_new_brand_runbooks_use_bootstrap_and_leave_live_tanaka_untouched():
    for filename in ("SETUP_MEMOS.md", "SETUP_VELVET.md"):
        runbook = (ROOT / filename).read_text(encoding="utf-8")
        assert "supabase/bootstrap.sql" in runbook
        assert "No ejecutes `supabase/upgrade_existing_tanaka.sql`" in runbook
        assert "Railway Tanaka" in runbook


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
