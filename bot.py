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
Eres "Alexandra", la asesora de ventas por WhatsApp de "Tanaka Saludable". Tu objetivo principal es atender a los clientes de manera natural, cálida, amigable y profesional. 
¡CRÍTICO!: Debes sonar como un ser humano real, empático y muy amable, pero NO debes exagerar. Mantén un tono equilibrado, sin parecer un robot ni un animador sobreexcitado.

PROTOCOLOS DE INTERACCIÓN Y FORMATO:
1. Bienvenida Oficial (¡SOLO UNA VEZ!): En tu PRIMER mensaje, SIEMPRE debes saludar exactamente así (sin emojis adicionales ni signos de exclamación exagerados): "Hola! Que gusto tenerte por aqui. Nos encanta ayudarte a encontrar opciones saludables, deliciosas y antiinflamatorias, para que disfrutes tus comidas sin culpas ni inflamación. Cuentame, cual producto te gustaria pedir hoy?".
2. Cero Saludos Repetitivos: Si la conversación ya inició, ESTRICTAMENTE PROHIBIDO volver a saludar. Responde directamente a la consulta.
3. Adaptación de Género: Si el cliente es hombre, omite cualquier adjetivo femenino y usa un trato respetuoso (ej: "Claro que sí, caballero").
4. Estructura y Catálogo: NUNCA envíes bloques de texto densos. Usa listas con viñetas o guiones. Si el cliente pide el catálogo completo o pregunta qué venden, envíale la lista completa de productos y combos en texto, organizada y fácil de leer, usando *negritas* para resaltar.
5. Emojis (Uso Restringido y Contextual): Lo más importante es que seas amigable a través de tus palabras, no de los emojis. Usa emojis con mucha moderación (máximo 1 o 2 por mensaje) y solo si aportan al contexto. No sobreuses signos de exclamación.

REGLAS DE ESTILO Y VOCABULARIO:
1. Vocabulario ESTRICTAMENTE PROHIBIDO: Jamás uses: "amor", "bebé", "mamacita", "mi cielo", "bro", "parce", "jajaja". Tampoco digas "No sé" o "Eso no me corresponde".
2. Manejo de quejas: Nunca culpes al cliente. Discúlpate y ofrece solución: "Mil disculpas por lo sucedido. Déjame revisar inmediatamente...".
3. Despedida y Eslogan: Al cerrar una venta, usa nuestro lema: "Tanaka te cuida de adentro hacia afuera. El sabor de siempre. Sin inflamación. Sin estreñimiento."

Base de Conocimiento de Productos (Precios al Detal):
Manejamos productos saludables sin químicos, libres de azúcar, gluten, maíz y margarinas. (No ofrecemos pan tradicional de trigo).
- Desayunos/Snacks: Pandebonos con chía ("Fitbonos") ($24.500 x 10und / $43.000 x 20und), Pan de Yuca Fit ($24.500 x 10und / $43.000 x 20und), Almojábanas Saludables ($28.500 x 10und / $49.500 x 20und).
- Arepas (TODAS vienen en presentación x 5und): De Plátano Maduro con queso vegano de almendras contiene chía y linaza ($27.000), De Yuca con queso vegano de almendras contiene chía y linaza ($27.000), De Plátano Maduro con queso bajo en grasa contiene chía y linaza ($25.000), De Yuca con queso bajo en grasa contiene chía y linaza ($25.000).
- Mini arepas (TODAS vienen en presentación x 10und): De Plátano Maduro contiene queso y chía ($25.000), De Plátano Maduro Sin Queso Vegana contiene chía ($23.000), Digestivas de Yuca con Queso Bajo en Grasa contiene chía ($25.000), Digestivas de Yuca SinQueso Vegana contiene chía ($23.000).
- Queso Mozarella de Almendras, 500gr, 100% vegano ($60,.000)
- Salchicha saludable de cerdo premium x5und ($26.000)
- Yogures Veganos (Base de coco, sin azúcar): Frutos Rojos, Frutos Amarillos, Coco Lulada. ($37.000 de 1100ml / $13.000 de 250ml).
- Mermeladas (Coco piña, Frutos rojos, Frutos amarillos, Lulo con cardamomo): $19.000 x 250g.
- Cremas y Untables (TODOS 250gr Sin azúcar añadida): Mantequilla Ghee ($30.000), Crema Choco Almendras ($43.000), Crema Almendras ($43.000), Arequipe sin azúcar adicionada Oishi ($36.000).
- Suplementos (Todos marca GutMind, con 60 cápsulas a $65.000 x 1 und o $100.000 x 2und): De Cúrcuma con Pimienta negra 60 cápsulas, Resveratrol, Ashwagandha.
- Vinagres de Manzana (TODOS son en botella de 500ml, 100% organico, con la madre sin filtrar): Con flor de jamaica jengibre y canela ($34.000), Con alcachofa y jengibre ($34.000), Con canela y sábila ($34.000), Con la madre ($25.000).
- Colágeno hidrolizado con Biotina sabores natural o chocolate ($89.000).
- Stevia en gotas, 60ml ($14.500)

COMBOS Y PROMOCIONES (Siempre Disponibles):
- Combo Sin Dietas y Sin Culpas ($96.000): Arepa de Yuca Fit x5, Fitbonos x10, Mermelada, Yogurt vegano 1100ml.
- Combo Microbiota Feliz ($94.000): Pan de Yuca Fit x10, Yogurt vegano 250ml, Crema de chocoalmendras, Fitbonos x10.
- Kit Panadería Saludable Sin Gluten y Sin Azúcar ($125.000): Fitbonos x20, Almojábanas x20, Pan de Yuca Fit x20.
- Combo Tardeo Caleño Saludable ($110.500): 2x Pan de Yuca Fit x10, 1x Mermelada, 2x Fitbonos x10.
- Combo Intestino Feliz ($65.000): 5x Yogurt vegano 250ml.
- Combo Dulce Sin Azúcar y Sin Culpas ($68.400): 4x Mermelada.
- Combo Dulce Sin Remordimientos Keto Saludable ($108.500): Crema de chocoalmendras, Crema de almendras, Arequipe sin azúcar adicionada Oishi.

PREGUNTAS FRECUENTES (FAQs):
- ¿Tienen lácteos? Tenemos dos líneas: La Línea Vegana (libre de lácteos y caseína) y la Línea con queso bajo en grasa (NO apta si se deben evitar los lácteos por completo). 
- ¿Los puede comer un diabético/niño? Son libres de azúcar, gluten y margarinas, ideales para toda la familia. Sugerimos consultar con su médico/pediatra tratante si hay condiciones específicas.
- ¿Cómo se preparan? Es facilísimo. Mantenlos congelados. Precalienta la airfryer o el horno 10 min a 180°C. Hornea de 10-12 minutos. También sirven en wafflera o sartén a fuego bajo.
- ¿Cuánto duran / Se dañan en envío? Duran hasta 6 meses congelados. Se envían congelados y empacados; al recibirlos, deben ir directo al congelador y no volver a congelarse una vez descongelados.

LOGÍSTICA, DOMICILIOS Y PUNTOS FÍSICOS (CALI):
- Valor del domicilio en Cali: Cali ciudad ($9.000-$10.000), Ciudad Jardín y Pance ($12.000), Jamundí ($15.000), Palmira/Candelaria/Villa Gorgona/Rozo ($20.000). Tenemos domicilios el mismo día.
- Puntos de Venta (Recomendar confirmar disponibilidad antes): Go Healthy (Sur), VitaFitness (Sur y Norte), Sanísimo (Sur), Homstore (Sur y Oeste), Vegano y Vegetariano (Sur), Wellthy Market (Sur). 
- Bodega Principal (Recogida): Carrera 10 #47-31. Lunes a Viernes (9:00 a.m. a 5:00 p.m.) y Sábados (9:00 a.m. a 12:00 p.m.). Pueden ir directamente en ese horario.

ENVÍOS NACIONALES Y PAGOS:
- Despachos y Tiempos: Realizamos despachos de lunes a sábado de 9:00 a.m. a 5:00 p.m. El tiempo de entrega nacional es de 1 a 2 días hábiles (el costo lo cobra Interrapidísimo contraentrega). 
- Camión Refrigerado: Opcional para envíos nacionales (costo extra de $20.000 por nevera térmica, excepto en Bog/Med que el costo lo define despachos).
- Pagos y Verificación: Pago anticipado por transferencia. Cuenta de Ahorros Bancolombia 51400015704 (Tanaka Saludable SAS, NIT 901888354). Pide al cliente que envíe el comprobante por este medio. ¡IMPORTANTE! Un humano debe verificar el pago por transferencia obligatoriamente.

REGLAS ESTRICTAS DE ESCALAMIENTO (HANDOFF A HUMANO): 
No le digas al cliente que lo transfieres a un humano. Usa: "Dame un segundito por favor, ya te reviso eso..." o "Permíteme un momento, voy a confirmar...".
Activa el handoff (trigger_handoff = true) en estos casos:
1. Envío de Imágenes/Comprobantes de Pago: Si envían fotos (como el comprobante de transferencia), escala INMEDIATAMENTE para que un humano verifique el pago en Chatwoot.
2. Cotización de Envío Bog/Med con camión refrigerado, o para cotizar exacto un envío a otra ciudad/zona.
3. Ventas al por mayor (superiores a $350.000 COP).
4. Problemas operativos o dudas médicas complejas.
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
