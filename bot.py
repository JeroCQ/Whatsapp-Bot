from database import get_or_create_customer_state, update_bot_state, pause_bot_for_handoff
import re

def process_message_logic(phone: str, text: str, is_image: bool = False) -> str:
    """
    Retorna el texto que el bot debe enviar de vuelta, 
    o None si el bot está pausado y no debe responder.
    """
    state_record = get_or_create_customer_state(phone)
    if not state_record:
        return "Disculpa, tuvimos un problema técnico. ¿Puedes intentarlo de nuevo?"
        
    # 🚨 REGLA DE ORO: Si el bot está pausado, lo ignoramos por completo
    if state_record["is_paused"]:
        print(f"Mensaje ignorado de {phone} porque is_paused=True")
        return None 

    current_state = state_record["current_state"]
    text_lower = text.lower() if text else ""

    # --- 1. DISPARADORES GLOBALES DE HANDOFF (Se evalúan siempre) ---
    
    # A. Intención de comprar al por mayor
    if any(word in text_lower for word in ["mayor", "mayorista", "distribuidor", "volumen"]):
        pause_bot_for_handoff(phone, "Intención de compra al por mayor")
        return "¡Hola! Para compras al por mayor y precios de distribuidor te voy a comunicar directamente con uno de nuestros asesores. Dame un momento por favor."
        
    # B. Pide humano explícitamente
    if any(word in text_lower for word in ["humano", "asesor", "persona", "ayuda"]):
        pause_bot_for_handoff(phone, "Solicitó hablar con un asesor")
        return "Con gusto. Te estoy transfiriendo con un asesor de servicio al cliente. En breve te responderán por este mismo chat."

    # C. Envío de Comprobante (Imagen o palabra clave)
    if is_image or any(word in text_lower for word in ["transferí", "recibo", "comprobante", "consignación"]):
        pause_bot_for_handoff(phone, "Envió posible comprobante de pago")
        return "¡Recibimos tu comprobante! 🧾 Un asesor va a verificar el pago en nuestra cuenta bancaria en este momento para confirmar tu pedido. Por favor espera."

    # --- 2. FLUJO NORMAL (STATE MACHINE) ---

    if current_state == "GREETING":
        update_bot_state(phone, "PRODUCT_SELECTION")
        return (
            "¡Hola! Bienvenido a nuestra tienda de Lácteos 🥛.\n\n"
            "Manejamos ventas al detal y al por mayor (recogida en nuestra bodega).\n\n"
            "¿Qué producto buscas hoy?\n"
            "1️⃣ Cuajada\n"
            "2️⃣ Crema\n"
            "(Responde con el número o el nombre)"
        )

    elif current_state == "PRODUCT_SELECTION":
        # Aquí idealmente guardaríamos el producto en la tabla orders, pero para simplificar el estado:
        update_bot_state(phone, "FORMAT_SELECTION")
        return (
            "¡Perfecto! ¿En qué presentación lo necesitas?\n"
            "• Libra\n"
            "• Kilo\n"
            "• Redondos\n"
            "(Escribe la presentación que prefieras)"
        )

    elif current_state == "FORMAT_SELECTION":
        update_bot_state(phone, "QUANTITY_SELECTION")
        return "¿Qué cantidad necesitas? (Por favor escribe un número, ej: 2)"

    elif current_state == "QUANTITY_SELECTION":
        update_bot_state(phone, "PAYMENT_PENDING")
        # Aquí calcularías el total real basado en la BD
        return (
            "¡Excelente! Tu pedido está pre-confirmado.\n\n"
            "💰 *Método de Pago:*\n"
            "Solo aceptamos transferencias bancarias.\n"
            "Banco: Bancolombia (Ahorros)\n"
            "Cuenta: 123-456789-00\n\n"
            "📸 *IMPORTANTE:* Por favor responde a este mensaje enviando la FOTO de tu comprobante de pago para que un asesor valide la transferencia.\n\n"
            "📍 Para recoger, debes acercarte a nuestra bodega y *tocar o timbrar físicamente* para ser atendido."
        )

    # Si llegamos aquí sin match o si está en PAYMENT_PENDING y escribió algo que no es imagen
    return "Para avanzar, por favor envía la foto de tu comprobante de pago, o escribe 'asesor' si necesitas ayuda humana."
