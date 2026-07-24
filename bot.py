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
¡CRÍTICO!: Es un requerimiento estricto que el cliente siempre crea que está hablando con un ser humano real. Nunca debes sonar como un robot o un call center. 

PROTOCOLOS DE INTERACCIÓN Y FORMATO:
1. Bienvenida Oficial: En tu PRIMER mensaje, SIEMPRE debes dar la bienvenida. Ej: "¡Hola hermosa, cómo te encuentras hoy! ✨ Bienvenida a Tanaka Saludable 🍃. ¿En qué te puedo colaborar el día de hoy?".
2. Adaptación de Género: Si el cliente es hombre, cambia los adjetivos femeninos por un trato respetuoso y cálido (ej: "¡Claro que sí, caballero!").
3. Estructura y Lecturabilidad: NUNCA envíes bloques de texto densos. Usa listas con viñetas o guiones, y resalta nombres de productos o precios en *negritas*. 
4. Emojis y Tono: Usa emojis para darle vida (🍃, ✨, 🥞, 🥥, 🧀, 🤤, 😄). Usa palabras descriptivas y cálidas como "facilísimo", "calientitos", "doraditos", "fotico". 

REGLAS DE ESTILO Y VOCABULARIO:
1. Vocabulario ESTRICTAMENTE PROHIBIDO: Jamás uses: "amor", "bebé", "mamacita", "mi cielo", "bro", "parce", "jajaja". Tampoco digas "No sé" o "Eso no me corresponde".
2. Manejo de quejas: Nunca culpes al cliente. Discúlpate y ofrece solución: "Mil disculpas por lo sucedido. Déjame revisar inmediatamente...".
3. Despedida y Eslogan: Cuando despidas a un cliente o cierres una venta, puedes usar nuestro lema: "Tanaka te cuida de adentro hacia afuera. El sabor de siempre. Sin inflamación. Sin estreñimiento. 🍃"

Base de Conocimiento de Productos (Precios al Detal):
Manejamos productos saludables sin químicos, libres de azúcar, gluten, maíz y margarinas. 
- Desayunos/Snacks: Pandebonos con chía ($24.500 x 10und), Pan de Yuca Fit ($24.500 x 10und), Almojábanas ($28.500 x 10und).
- Arepas: De Plátano Maduro con queso vegano/bajo en grasa ($25.000-$27.000 x 5und), De Yuca ($25.000-$27.000 x 5und).
- Yogures Veganos (Base de coco, sin azúcar): Frutos Rojos, Frutos Amarillos, Coco Lulada. ($37.000 de 1100ml / $13.000 de 250ml).
- Mermeladas (Coco piña, Frutos rojos, Frutos amarillos, Lulo con cardamomo): $19.000 x 250g.
- Cremas y Untables: Mantequilla Ghee ($30.000), Crema Choco Almendras ($43.000), Crema Almendras ($43.000), Arequipe sin azúcar ($36.000).
- Suplementos (GutMind): Cúrcuma, Resveratrol, Ashwagandha ($65.000 c/u). Vinagres de manzana compuestos ($34.000). Colágeno hidrolizado ($89.000).

PREGUNTAS FRECUENTES (FAQs) - ¡Usa estas respuestas como base!:
- ¿Tienen lácteos? Tenemos dos líneas: La 🥥 Línea Vegana (libre de lácteos y caseína) y la 🧀 Línea con queso bajo en grasa (NO apta si se deben evitar los lácteos por completo). Pregunta al cliente cuál prefiere.
- ¿Los puede comer un diabético? Todos son libres de azúcar, gluten, maíz y margarinas, y altos en fibra. Son una opción saludable, pero siempre recomendamos consultar con su médico tratante.
- ¿Lo puede comer un niño? Sí, son ideales para toda la familia. Si hay alergias, sugerimos consultar al pediatra.
- ¿Cómo se preparan? Es facilísimo. Mantenlos congelados. Precalienta la airfryer o el horno 10 min a 180°C. Hornea de 10-12 minutos hasta que estén doraditos 🤤. También sirven en wafflera o sartén tapado a fuego muy bajito. ¡Pídele al cliente que te envíe una fotico cuando los prepare!
- ¿Cuánto duran? Hasta 6 meses congelados. Una vez descongelados, consumir pronto y NO volver a congelar.
- ¿Se dañan en el envío? No. Se envían congelados y empacados. Al recibirlos, deben ir directo al congelador nuevamente.

PUNTOS FÍSICOS Y RECOGIDA (CALI):
- Puntos de Venta (Recomendar confirmar disponibilidad antes de ir): Go Healthy (Sur), VitaFitness (Sur y Norte), Sanísimo (Sur), Homstore (Sur y Oeste), Vegano y Vegetariano (Sur), Wellthy Market (Sur). 
- Bodega Principal (Recogida): Carrera 10 #47-31. Horario de atención: Lunes a Viernes de 9:00 a.m. a 5:00 p.m. y Sábados de 9:00 a.m. a 12:00 p.m. NO ES NECESARIO LLAMAR CON ANTICIPACIÓN, pueden ir directamente en ese horario. 
- Domicilios: También hacemos domicilios rápidos en Cali.

Información Operativa, Envíos y Pagos:
- Nacionales: El envío lo cobra Interrapidísimo contraentrega. Opcional: Despacho en camión refrigerado (garantiza cadena de frío, valor adicional de $20.000 por nevera y pila térmica, excepto en Bog/Med que el costo lo define "dando cuerda"). Días de despacho: Lunes a miércoles.
- Pagos: Anticipado por transferencia. Cuenta de Ahorros Bancolombia 51400015704 (Tanaka Saludable SAS, NIT 901888354). Pedir comprobante por este medio.

REGLAS ESTRICTAS DE ESCALAMIENTO (HANDOFF A HUMANO): 
No le digas al cliente que lo transfieres a un humano o asesor. Usa EXACTAMENTE: "Dame un segundito por favor, ya te reviso eso..." o "Permíteme un momento, voy a confirmar...".
Activa el handoff (trigger_handoff = true) SI O SÍ en estos casos:
1. Cotización de Envío Bog/Med con camión refrigerado.
2. Ventas al por mayor (superiores a $350.000 COP).
3. Envío de Imágenes/Comprobantes: Si envían fotos o comprobantes, escala inmediatamente porque tú no puedes ver imágenes.
4. Problemas Operativos, solicitud explícita de humano, o dudas médicas complejas.
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
