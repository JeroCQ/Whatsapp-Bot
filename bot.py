import json
from pydantic import BaseModel
from google import genai
from google.genai import types
from config import config
from database import (
    get_or_create_customer_state, 
    pause_bot_for_handoff, 
    save_message_log, 
    get_message_logs
)

# 1. Inicializar el cliente con el nuevo SDK
client = genai.Client(api_key=config.GEMINI_API_KEY)

# 2. Definir el esquema estricto que obligará a Gemini a no equivocarse con el JSON
class BotResponse(BaseModel):
    response: str
    trigger_handoff: bool
    handoff_reason: str

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
"""

def process_message_logic(phone: str, text: str, is_image: bool = False) -> str:
    """
    Usa Gemini para procesar el mensaje, entender el contexto y decidir si hace handoff.
    """
    state_record = get_or_create_customer_state(phone)
    if not state_record:
        return "Disculpa, tuvimos un problema técnico. ¿Puedes intentarlo de nuevo?"
        
    # Si el bot está pausado, no hacemos nada
    if state_record["is_paused"]:
        print(f"Mensaje ignorado de {phone} porque is_paused=True")
        return None 

    # Guardar el mensaje entrante
    user_input_to_log = "[El usuario envió una imagen/comprobante]" if is_image else text
    save_message_log(phone, "user", user_input_to_log)

    # Recuperar el historial
    history = get_message_logs(phone, limit=8)
    formatted_history = [f"{'Usuario' if msg['role'] == 'user' else 'Bot'}: {msg['content']}" for msg in history]
    context_str = "\n".join(formatted_history)

    # Construir el prompt
    prompt = f"""
    Historial de la conversación reciente:
    {context_str}

    Indicaciones de este turno:
    - ¿El usuario envió una imagen en este turno?: {"SÍ" if is_image else "NO"}.
    - Mensaje actual del usuario: "{text if not is_image else '[Imagen/Archivo]'}"

    Analiza la situación y genera la respuesta.
    """

    try:
        # 3. Llamar al modelo con la configuración de Salida Estructurada (Schema)
        response = client.models.generate_content(
            model="gemini-flash-latest",
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_INSTRUCTION,
                response_mime_type="application/json",
                response_schema=BotResponse,
                temperature=0.2, # Temperatura baja para que sea más determinista y estable
            ),
        )
        
        # Como usamos el schema, response.text es 100% un JSON válido
        ai_data = json.loads(response.text)
        
        response_text = ai_data.get("response", "")
        trigger_handoff = ai_data.get("trigger_handoff", False)
        reason = ai_data.get("handoff_reason", "Transferencia por IA")

        if response_text:
            save_message_log(phone, "model", response_text)

        if trigger_handoff:
            print(f"[IA HANDOFF TRIGGERED] Razón: {reason}")
            pause_bot_for_handoff(phone, reason)

        return response_text

    except Exception as e:
        import traceback
        print(f"[ERROR GEMINI] Falló la inferencia con Gemini:")
        traceback.print_exc()
        
        if is_image:
            pause_bot_for_handoff(phone, "Envío de comprobante (Fallback)")
            return "¡Recibimos tu comprobante! Un asesor va a verificar el pago en nuestra cuenta bancaria en este momento. Por favor espera."
        return "Disculpa, en este momento estoy teniendo un retraso en procesar tu mensaje. ¿Podrías escribir nuevamente?"
