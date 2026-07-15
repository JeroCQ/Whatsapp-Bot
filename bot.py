import json
import google.generativeai as genai
from config import config
from database import (
    get_or_create_customer_state, 
    pause_bot_for_handoff, 
    save_message_log, 
    get_message_logs
)

# Configurar la API de Gemini
genai.configure(api_key=config.GEMINI_API_KEY)

# Definimos las instrucciones del sistema del Agente
SYSTEM_INSTRUCTION = """
Eres un asistente de WhatsApp inteligente y amable para una tienda de Lácteos en Colombia.
Tu objetivo es ayudar al usuario a realizar su pedido de manera fluida y decidir cuándo transferir la conversación a un humano.

INFORMACIÓN DEL NEGOCIO:
- Productos principales: Cuajada y Crema.
- Presentaciones: Libra, Kilo y Redondos.
- Método de Pago: Únicamente transferencia bancaria a Bancolombia Ahorros 123-456789-00. El cliente debe enviar una foto del comprobante de pago por WhatsApp para validar.
- Entrega/Recogida: Los pedidos se recogen en nuestra bodega física. Indícale al usuario que debe avisar antes de llegar y que, cuando esté en la bodega, debe tocar o timbrar físicamente para ser atendido.

REGLAS DE TRANSFERENCIA HUMANA (HANDOFF) EN CHATWOOT:
Debes activar de forma inmediata el trigger_handoff (= true) y asignar un handoff_reason descriptivo si detectas cualquiera de las siguientes situaciones:
1. Compras al Por Mayor: El usuario expresa interés en comprar al por mayor, precios de distribuidor, bultos, volumen o negociaciones comerciales.
   - handoff_reason: "Interés en compras al por mayor"
2. Comprobante de Pago enviado o mencionado: El usuario dice que ya pagó, que envió la transferencia, adjuntó el recibo, o la conversación indica que envió un comprobante (nota: si viene un flag 'is_image: True', es un comprobante).
   - handoff_reason: "Envío de comprobante de pago"
3. Solicitud Directa de Asesor: El usuario pide explícitamente hablar con un humano, asesor, operador, persona real o soporte.
   - handoff_reason: "Solicitud directa de asesor"
4. Frustración, enojo o confusión: El usuario está molesto, tiene un reclamo o no logras entender su solicitud tras varios intentos.
   - handoff_reason: "Usuario frustrado o queja técnica"

IMPORTANTE:
Debes responder SIEMPRE en formato JSON con la siguiente estructura exacta:
{
  "response": "Tu respuesta amable en español de Colombia al cliente. Si vas a transferir al usuario a un asesor, explícale de forma atenta que lo vas a comunicar con un humano y despídete brevemente.",
  "trigger_handoff": true/false,
  "handoff_reason": "Si trigger_handoff es true, coloca aquí la razón del handoff. De lo contrario, deja un string vacío \\"\\""
}
"""

def process_message_logic(phone: str, text: str, is_image: bool = False) -> str:
    """
    Usa Gemini para procesar el mensaje, entender el contexto y decidir si hace handoff.
    """
    state_record = get_or_create_customer_state(phone)
    if not state_record:
        return "Disculpa, tuvimos un problema técnico. ¿Puedes intentarlo de nuevo?"
        
    # 🚨 REGLA DE ORO: Si el bot está pausado, no hacemos nada
    if state_record["is_paused"]:
        print(f"Mensaje ignorado de {phone} porque is_paused=True")
        return None 

    # 1. Guardar el mensaje entrante del usuario en la base de datos
    user_input_to_log = "[El usuario envió una imagen/comprobante]" if is_image else text
    save_message_log(phone, "user", user_input_to_log)

    # 2. Recuperar el historial reciente de la conversación
    history = get_message_logs(phone, limit=8)
    
    # 3. Formatear el historial para que Gemini lo procese
    formatted_history = []
    for msg in history:
        role_label = "Usuario" if msg["role"] == "user" else "Bot"
        formatted_history.append(f"{role_label}: {msg['content']}")
    
    context_str = "\n".join(formatted_history)

    # 4. Construir el prompt para Gemini
    prompt = f"""
    Historial de la conversación reciente:
    {context_str}

    Indicaciones de este turno:
    - ¿El usuario envió una imagen en este turno?: {"SÍ" if is_image else "NO"}.
    - Mensaje actual del usuario: "{text if not is_image else '[Imagen/Archivo]'}"

    Analiza la situación actual basándote en las instrucciones del sistema, genera la respuesta adecuada y evalúa si es necesario hacer handoff a un humano. Recuerda responder únicamente con el objeto JSON estructurado.
    """

    try:
        # Inicializar y llamar al modelo Gemini
        model = genai.GenerativeModel(
            model_name="gemini-flash-latest",
            system_instruction=SYSTEM_INSTRUCTION
        )
        
        # Forzar salida en formato JSON
        raw_response = model.generate_content(
            prompt,
            generation_config={"response_mime_type": "application/json"}
        )
        
        # Parsear la respuesta estructurada de Gemini
        ai_data = json.loads(raw_response.text.strip())
        
        response_text = ai_data.get("response", "")
        trigger_handoff = ai_data.get("trigger_handoff", False)
        reason = ai_data.get("handoff_reason", "Transferencia por IA")

        # 5. Guardar la respuesta del modelo en el historial de base de datos
        if response_text:
            save_message_log(phone, "model", response_text)

        # 6. Si la IA detectó que se requiere un humano, pausamos el bot en Supabase
        if trigger_handoff:
            print(f"[IA HANDOFF TRIGGERED] Razón: {reason}")
            pause_bot_for_handoff(phone, reason)

        return response_text

    except Exception as e:
        print(f"[ERROR GEMINI] Falló la inferencia con Gemini: {e}")
        # Plan de contingencia clásico por si la API falla o devuelve un JSON corrupto
        if is_image:
            pause_bot_for_handoff(phone, "Envío de comprobante (Fallback)")
            return "¡Recibimos tu comprobante! Un asesor va a verificar el pago en nuestra cuenta bancaria en este momento para confirmar tu pedido. Por favor espera."
        return "Disculpa, en este momento estoy teniendo un retraso en procesar tu mensaje. ¿Podrías escribir nuevamente?"
