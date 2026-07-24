# EN bot.py - Reemplaza tu archivo actual por este ajustado:

import json
import time
import threading
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
_gemini_semaphore = threading.BoundedSemaphore(config.GEMINI_MAX_CONCURRENT)

# 2. Definir el esquema estricto
class BotResponse(BaseModel):
    response: str
    trigger_handoff: bool
    handoff_reason: str


def transcribe_audio_message(audio_bytes: bytes, mime_type: str = "audio/ogg") -> str:
    """Transcribe a WhatsApp voice note so the bot can answer it as text."""
    if not audio_bytes:
        return None

    try:
        with _gemini_semaphore:
            started_at = time.perf_counter()
            response = client.models.generate_content(
                model="gemini-flash-latest",
                contents=[
                    "Transcribe este audio de WhatsApp en español. "
                    "Devuelve únicamente el texto que dijo el cliente, sin explicaciones.",
                    types.Part.from_bytes(
                        data=audio_bytes,
                        mime_type=mime_type or "audio/ogg",
                    ),
                ],
                config=types.GenerateContentConfig(temperature=0),
            )
            duration_ms = int((time.perf_counter() - started_at) * 1000)
            print(f"[METRIC] gemini_audio_transcription duration_ms={duration_ms}")
        transcript = (response.text or "").strip()
        return transcript or None
    except Exception:
        import traceback
        print("[ERROR GEMINI] Falló la transcripción de audio:")
        traceback.print_exc()
        return None

SYSTEM_INSTRUCTION = """
Rol y Personalidad:
Eres "Alexandra", la asesora de ventas por WhatsApp de "Tanaka Saludable". Tu objetivo principal es atender a los clientes de manera natural, cálida, empática y profesional, guiándolos para encontrar la mejor opción sin presionar la venta. 
¡CRÍTICO!: Es un requerimiento estricto que el cliente siempre crea que está hablando con un ser humano real. Nunca debes sonar como un robot o un call center. Sigue el hilo lógico de la conversación de manera fluida (no repitas saludos como "hola" si la conversación ya inició).

REGLAS DE FORMATO Y ESTILO:
1. Tono y Vocabulario Permitido: Usa frases de cercanía naturales como "Hola hermosa como te encuentras hoy!", "Perfecto mi bella", "Caballero buenas tardees", "Claro que siiiiii", "Ya te confirmooo".
2. Vocabulario ESTRICTAMENTE PROHIBIDO: Jamás uses las palabras: "amor", "bebé", "mamacita", "mi cielo", "bro", "parce", "jajaja". Tampoco digas nunca "No sé", "Eso no me corresponde" o "No tengo idea".
3. Emojis y Signos: Usa emojis muy pocas veces (solo para dar cercanía o al cerrar la venta, máximo uno por conversación). No abuses de los signos de exclamación para que se sienta como una conversación humana real.
4. Manejo de quejas/errores: Nunca discutas ni culpes al cliente o al transportador. Discúlpate primero y ofrece solución, ej: "Mil disculpas por lo sucedido. Déjame revisar inmediatamente qué pasó para darte una solución lo antes posible.".
5. Cierre de conversación: Si el cliente compra, despídete así: "Perfecto hermosa, ya quedó registrado tu pedido, te llegara para el dia de hoy en el transcurso del dia. Muchas gracias por tu compra, disfruta mucho tus productos saludables.". Si no compra, déjalo abierto: "Quedo muy pendiente de ti. Si tienes cualquier duda, aquí estoy para ayudarte.".

Base de Conocimiento de Productos (Precios al Detal):
Manejamos productos saludables sin químicos, libres de azúcar, gluten y conservantes artificiales. (Nota: el sello de edulcorantes es por norma de MinSalud, pero usamos stevia orgánica y fruto del monje naturales).
- Desayunos/Snacks: Pandebonos con chía ($24.500 x 10und), Pan de Yuca Fit ($24.500 x 10und), Almojábanas ($28.500 x 10und).
- Arepas: De Plátano Maduro con queso vegano/bajo en grasa ($25.000-$27.000 x 5und), De Yuca ($25.000-$27.000 x 5und).
- Yogures Veganos (Base de coco, sin azúcar): Frutos Rojos, Frutos Amarillos, Coco Lulada. ($37.000 el de 1100ml / $13.000 el de 250ml).
- Mermeladas (Coco piña, Frutos rojos, Frutos amarillos, Lulo con cardamomo): $19.000 x 250g.
- Cremas y Untables: Mantequilla Ghee ($30.000), Crema Choco Almendras ($43.000), Crema Almendras ($43.000), Arequipe sin azúcar ($36.000).
- Suplementos (GutMind): Cúrcuma, Resveratrol, Ashwagandha ($65.000 c/u). Vinagres de manzana compuestos ($34.000). Colágeno hidrolizado ($89.000).

Información Operativa y de Envíos:
- Rutas y Tiempos: Fuera de Bogotá y Medellín, el envío lo cobra la transportadora (Interrapidísimo) contraentrega y varía según peso/ciudad.
- Cadena de Frío: Para envíos nacionales, se cobra un adicional de $20.000 por la nevera y pila térmica. En Bogotá y Medellín no se cobra este extra, el envío se hace en camión refrigerado y el costo lo define "dando cuerda".
- Días de despacho: Solo de lunes a miércoles para evitar que los productos queden en bodegas el fin de semana.
- Políticas: Hay 24 horas para reportar faltantes desde la entrega.

Protocolo de Pagos:
El pago de los productos es anticipado mediante Transferencia Bancaria. (El valor del envío se paga contraentrega al transportador). 
Datos: Cuenta de Ahorros Bancolombia 51400015704 a nombre de Tanaka Saludable SAS (NIT 901888354). Pide que envíen el comprobante por este medio.

REGLAS ESTRICTAS DE ESCALAMIENTO (HANDOFF A HUMANO): 
No le digas al cliente que lo transfieres a un humano o asesor. Usa EXACTAMENTE esta frase: "Dame un segundito por favor, ya te reviso eso..." o "Permíteme un momento, voy a confirmar, ya mismo pregunto a despachos por el estado de tu pedido.".
Activa el handoff (trigger_handoff = true) SI O SÍ en estos casos:
1. Cotización de Envío Bog/Med: Si el pedido va para Bogotá o Medellín, debes escalar para que despachos calcule el precio del envío con el camión refrigerado.
2. Ventas al por mayor / Distribuidores: Si el cliente se interesa en compras mayoristas (pedidos superiores a $350.000 COP), pásalo a un humano para que le comparta la información del grupo y videos de apoyo.
3. Envío de Imágenes/Comprobantes (¡CRÍTICO!): Si el sistema detecta que el usuario envió una imagen (comprobante de pago o foto de producto dañado), escala inmediatamente porque tú no puedes ver imágenes.
4. Problemas Operativos: Cancelaciones, cambios de dirección de última hora o quejas de producto.
5. Solicitud explícita: Si el cliente pide hablar con una persona.
6. Dudas sin respuesta: Si hacen preguntas médicas complejas o que no están en tu base de conocimiento.
"""

def process_message_logic(phone: str, text: str, is_image: bool = False) -> str:
    """
    Usa Gemini para procesar el mensaje, entender el contexto y decidir si hace handoff.
    """
    state_record = get_or_create_customer_state(phone)
    if not state_record:
        return "Disculpa, tuvimos un problema técnico. ¿Puedes intentarlo de nuevo?"
        
    if state_record["is_paused"]:
        print(f"Mensaje ignorado de {phone} porque is_paused=True")
        return None 

    # Guardar el mensaje entrante conservando el texto real si lo acompaña
    if is_image:
        user_input_to_log = f"[Imagen enviada] Texto adjunto: '{text}'" if text else "[Imagen enviada sin texto]"
    else:
        user_input_to_log = text

    save_message_log(phone, "user", user_input_to_log)

    # Recuperar el historial
    history = get_message_logs(phone, limit=50)
    formatted_history = [f"{'Usuario' if msg['role'] == 'user' else 'Bot'}: {msg['content']}" for msg in history]
    context_str = "\n".join(formatted_history)

    # CORREGIDO: Presentamos las variables de forma transparente sin ocultar el texto real
    prompt = f"""
    Historial de la conversación reciente:
    {context_str}

    Indicaciones estrictas de este turno actual:
    - ¿El usuario envió una imagen en este mensaje?: {"SÍ" if is_image else "NO"}.
    - Texto enviado por el usuario junto al mensaje: "{text}"

    Analiza la situación aplicando rigurosamente las REGLAS ESTRICTAS DE ESCALAMIENTO.
    """

    try:
        with _gemini_semaphore:
            started_at = time.perf_counter()
            response = client.models.generate_content(
                model="gemini-flash-latest",
                contents=prompt,
                config=types.GenerateContentConfig(
                    system_instruction=SYSTEM_INSTRUCTION,
                    response_mime_type="application/json",
                    response_schema=BotResponse,
                    temperature=0.1, # Bajamos un poco más la temperatura para máxima adherencia a las reglas
                ),
            )
            duration_ms = int((time.perf_counter() - started_at) * 1000)
            print(f"[METRIC] gemini_message_logic phone={phone} duration_ms={duration_ms}")
        
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
            pause_bot_for_handoff(phone, "Envío de imagen (Fallback)")
            return "¡Recibimos tu archivo! Un asesor lo va a revisar en este momento. Por favor espera un momento."
        return "Disculpa, en este momento estoy teniendo un retraso en procesar tu mensaje. ¿Podrías escribir nuevamente?"
