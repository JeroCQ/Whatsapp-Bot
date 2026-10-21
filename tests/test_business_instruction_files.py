from pathlib import Path
import re
import unicodedata

import pytest


ROOT = Path(__file__).resolve().parents[1]


def normalized_instruction(business_id):
    instruction = (ROOT / f"src/clients/{business_id}/system_instruction.txt").read_text(encoding="utf-8")
    instruction = unicodedata.normalize("NFKD", instruction).encode("ascii", "ignore").decode().lower()
    return instruction.replace("**", "").replace("`", "")


def paragraphs_containing(instruction, text):
    rules = re.split(r"\n\s*\n|\n(?=\s*(?:\* |- |\d+\. ))", instruction)
    return [rule for rule in rules if text in rule]


def test_memos_instruction_is_available_to_the_selected_deployment():
    instruction = (ROOT / "src/clients/memos/system_instruction.txt").read_text(encoding="utf-8")
    assert "Quesos Memo's" in instruction
    assert "829-0002441-2" in instruction
    assert "follow_up_delay_minutes = 120" in instruction


def test_velvet_instruction_uses_confirmed_identity_catalog_and_logistics():
    instruction = (ROOT / "src/clients/velvet/system_instruction.txt").read_text(encoding="utf-8").replace("**", "")
    assert "Camila" in instruction
    assert "Velvet Repostería y Mochis Velvet" in instruction
    assert "Caja x 6 unidades: $28.000 COP" in instruction
    assert "De 40 a 95 unidades: $3.500 COP" in instruction
    assert "Caja x 12 unidades: $50.000 COP" in instruction
    assert "Desde 96 unidades en adelante: $2.900 COP" in instruction
    assert "Carrera 10 # 47-31, Barrio El Troncal, Cali" in instruction
    assert "Lunes a sábado de 9:00 a.m. a 6:00 p.m." in instruction
    assert "dentro de la ciudad que no pertenezcan a las zonas más lejanas" in instruction
    assert "$9.000 COP" in instruction
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
    assert "3025991292" not in instruction
    assert "tanakasaludablecali@gmail.com" not in instruction


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
    assert "Solo puede existir una secuencia activa por conversación" in instruction
    assert "B2B_HIGH_VALUE" in instruction
    assert "Después de un saludo, primera respuesta o catálogo" in instruction
    assert "Pregunta repetida o corrección" in instruction
    assert "súper pendiente" in instruction
    assert "hasta tres intentos" in instruction
    assert "entre las 8:00 a.m. y las 6:00 p.m." in instruction
    assert "usa 1 o 2 frases breves" in instruction
    assert "no una renuncia a vender" in instruction
    assert "recomendación pertinente" in instruction
    assert "no transcribas listas extensas" in instruction
    assert "envía únicamente el archivo específico pertinente" in instruction
    assert "no autoriza un seguimiento comercial por sí sola" in instruction


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
    assert "solo invita a etiquetar a Tanaka después de que el cliente haya comprado o recibido productos" in instruction


def test_tanaka_instruction_preserves_purchase_momentum_and_recognizes_returning_customers():
    instruction = (ROOT / "src/clients/tanaka/system_instruction.txt").read_text(encoding="utf-8")
    assert "Qué bueno tenerte de nuevo" in instruction
    assert "está guardado su nombre" in instruction
    assert "El momentum de compra es sagrado" in instruction
    assert "ubicación general mínima necesaria" in instruction
    assert "NO pidas todavía dirección exacta" in instruction
    assert "facturación electrónica son completamente opcionales" in instruction
    assert "https://www.instagram.com/tanakasaludable/?hl=es" in instruction
    assert "añade en una línea separada el enlace completo" in instruction


def test_tanaka_instruction_includes_official_business_contact_and_dispatch_details():
    instruction = (ROOT / "src/clients/tanaka/system_instruction.txt").read_text(encoding="utf-8")
    assert "Nombre legal:** Tanaka Saludable SAS" in instruction
    assert "Celular:** 3025991292" in instruction
    assert "Correo electrónico:** tanakasaludablecali@gmail.com" in instruction
    assert "Manager:** María Camila" in instruction
    assert "salen únicamente los lunes o martes" in instruction
    assert "Tardan aproximadamente dos días en llegar" in instruction
    assert "lunes a miércoles" not in instruction


@pytest.mark.parametrize("business_id", ["tanaka", "velvet"])
def test_business_payment_defaults_to_the_exact_shared_bold_key(business_id):
    instruction = normalized_instruction(business_id)
    bold_rules = paragraphs_containing(instruction, "@bold3187622894")

    assert bold_rules
    assert set(re.findall(r"@bold[a-z0-9_]*", instruction)) == {"@bold3187622894"}
    assert any(re.search(r"primera opcion|opcion principal|prioridad|primero", paragraph) for paragraph in bold_rules)
    assert not re.search(r"\b(?:llave|clave)\s+(?:bold\s+)?3187622894\b", instruction)


@pytest.mark.parametrize("business_id,own_account,other_account", [
    ("tanaka", "51400015704", "74194555052"),
    ("velvet", "74194555052", "51400015704"),
])
def test_business_bank_account_remains_isolated_and_only_an_alternative_on_request(
    business_id, own_account, other_account,
):
    instruction = normalized_instruction(business_id)
    bank_rules = paragraphs_containing(instruction, own_account)

    assert bank_rules
    assert other_account not in instruction
    assert all("bancolombia" in paragraph for paragraph in bank_rules)
    assert any(re.search(r"(?:solo|unicamente).*(?:solicit|pid|prefier|pida)|(?:solicit|pid|prefier|pida).*alternativ", paragraph) for paragraph in bank_rules)


@pytest.mark.parametrize("business_id", ["tanaka", "velvet"])
def test_business_card_terminal_is_available_only_in_person(business_id):
    instruction = normalized_instruction(business_id)
    terminal_rules = paragraphs_containing(instruction, "datafono")

    assert terminal_rules
    assert any(re.search(r"(?:solo|unicamente|exclusivamente).*presencial|presencial.*(?:solo|unicamente|exclusivamente)", paragraph) for paragraph in terminal_rules)
    assert "por el momento no se cuenta con pago con tarjeta de credito, datafono" not in instruction
    assert not any("datafono a domicilio" in paragraph and "no" not in paragraph for paragraph in terminal_rules)


@pytest.mark.parametrize("business_id", ["tanaka", "velvet"])
def test_business_payment_link_discloses_total_surcharge_and_requires_a_real_link(business_id):
    instruction = normalized_instruction(business_id)
    link_rules = paragraphs_containing(instruction, "link")
    surcharge_rules = [paragraph for paragraph in link_rules if re.search(r"2[,.]5\s*%", paragraph)]

    assert surcharge_rules
    assert any("total" in paragraph and "domicilio" in paragraph for paragraph in surcharge_rules)
    assert any(re.search(r"desglos|subtotal", paragraph) for paragraph in link_rules)
    assert any(re.search(r"no invent|nunca invent|no gener|no constru", paragraph) for paragraph in link_rules)
    assert any("handoff" in paragraph and re.search(r"no (?:hay|existe|esta|consta|tienes|cuentas)|falta|sin (?:un )?(?:link|enlace)", paragraph) for paragraph in link_rules)


@pytest.mark.parametrize("business_id", ["tanaka", "velvet"])
def test_business_pickup_uses_confirmed_location_and_six_pm_closing(business_id):
    instruction = normalized_instruction(business_id)
    pickup_rules = paragraphs_containing(instruction, "carrera 10")

    assert pickup_rules
    assert any(re.search(r"carrera 10\s*#\s*47-31", paragraph) and "troncal" in paragraph for paragraph in pickup_rules)
    assert any("base aerea" in paragraph for paragraph in pickup_rules)
    assert re.search(r"lunes a sabado,? de 9:00 a\.m\. a 6:00 p\.m\.", instruction)
    assert not any(re.search(r"lunes a viernes\s*\(9:00 a\.m\. a 5:00 p\.m\.\)", paragraph) for paragraph in pickup_rules)
    assert not any(re.search(r"sabados\s*\(9:00 a\.m\. a 12:00 p\.m\.\)", paragraph) for paragraph in pickup_rules)


@pytest.mark.parametrize("business_id", ["tanaka", "velvet"])
def test_business_delivery_cost_objection_offers_pickup_instead_of_unauthorized_discount(business_id):
    instruction = normalized_instruction(business_id)
    pickup_rules = [paragraph for paragraph in instruction.split("\n\n") if re.search(r"recog|recoger", paragraph)]

    assert any("domicilio" in paragraph and re.search(r"caro|costoso|costo|coste|valor", paragraph) and re.search(r"queja|objecion|caro|costoso", paragraph) for paragraph in pickup_rules)


@pytest.mark.parametrize("business_id", ["tanaka", "velvet"])
def test_new_payment_options_keep_human_verification_and_no_automatic_confirmation(business_id):
    instruction = normalized_instruction(business_id)
    payment_rules = paragraphs_containing(instruction, "comprobante")

    assert any("handoff" in paragraph and re.search(r"valid|verific", paragraph) for paragraph in payment_rules)
    assert any(re.search(r"(?:nunca|no).*(?:confirm|aprobad|pagad)", paragraph) for paragraph in payment_rules)


def test_velvet_payment_microclosures_do_not_restore_the_old_default_transfer_menu():
    instruction = normalized_instruction("velvet")
    assert "preguntando solo si prefiere nequi, bancolombia o daviplata" not in instruction
    assert "ofrece una sola vez nequi, bancolombia o daviplata como opciones" not in instruction
    assert "domicilio o recogida, nequi, bancolombia o daviplata" not in instruction
