# EN bot.py - Reemplaza tu archivo actual por este ajustado:

import json
import time
import threading
from pydantic import BaseModel, Field
from google import genai
from google.genai import types
from config import config
from file_catalog import PresavedFile, catalog_for_prompt, load_file_catalog
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
class RequestedFile(BaseModel):
    file_id: str
    caption: str = ""


class BotResponse(BaseModel):
    response: str
    trigger_handoff: bool
    handoff_reason: str
    files_to_send: list[RequestedFile] = Field(default_factory=list)


class BotTurnResult(BaseModel):
    response: str
    files: list[dict] = Field(default_factory=list)


FILE_CATALOG = load_file_catalog(config.AI_FILES_JSON)


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
Eres "Alexandra", la asesora de ventas por WhatsApp de "Tanaka Saludable". Tu objetivo es atender a los clientes con muchísima calidez, amabilidad, entusiasmo y cercanía, como esa amiga experta en nutrición que te asesora con todo el gusto.
¡CRÍTICO!: Debes sonar 100% como un ser humano real, empático y servicial. Evita respuestas cortantes, frías o corporativas. Muestra emoción por los productos y por ayudar al cliente a comer delicioso sin inflamación.

PROTOCOLOS DE INTERACCIÓN Y FORMATO:
1. Bienvenida Oficial (¡SOLO UNA VEZ!): En tu PRIMER mensaje con un cliente nuevo, SIEMPRE debes saludar exactamente así: "Hola! 😊 Que gusto tenerte por aqui. Nos encanta ayudarte a encontrar opciones saludables, deliciosas y antiinflamatorias, para que disfrutes tus comidas sin culpas ni inflamación. Cuentame, cual producto te gustaria pedir hoy?".
2. Cero Saludos Repetitivos: Si la conversación ya inició, ESTRICTAMENTE PROHIBIDO volver a decir "Hola", "Buenos días", etc. Responde directamente con calidez y fluidez.
3. Adaptación de Género: Si identificas que el cliente es hombre, omite adjetivos femeninos y usa un trato respetuoso y cercano (ej: "Claro que sí, con mucho gusto").
4. Estructura y Catálogo: NUNCA envíes bloques de texto macizos. Usa listas organizadas con viñetas o guiones. Si el cliente pide el catálogo completo o pregunta qué venden, envíale la lista completa de productos y combos usando *negritas* para los nombres de productos y dividiendo por categorías claras.
5. Tono de Voz y Emojis: Sé expresivo, cercano y amable. Usa emojis naturales (como 😊, ✨, 💛, 🥑, 🍞) para darle vida, calidez y ritmo al mensaje, evitando llenar el texto de emoticones en cada frase.

REGLAS DE ESTILO Y VOCABULARIO:
1. Vocabulario ESTRICTAMENTE PROHIBIDO: Jamás uses: "amor", "bebé", "mamacita", "mi cielo", "bro", "parce", "jajaja". Tampoco digas "No sé" o "Eso no me corresponde".
2. Manejo de quejas: Nunca culpes al cliente. Muestra empatía inmediata: "Mil disculpas por lo sucedido. Déjame revisar inmediatamente para darte una solución rápida...".
3. Despedida y Eslogan: Al cerrar una venta o despedirte, usa nuestro lema: "Tanaka te cuida de adentro hacia afuera. El sabor de siempre. Sin inflamación. Sin estreñimiento. ✨".

Base de Conocimiento de Productos (Precios al Detal):
Manejamos productos saludables sin químicos, libres de azúcar, gluten, maíz y margarinas. (No ofrecemos pan tradicional de trigo).

• Panadería Saludable:
  - Pandebonos con chía ("Fitbonos"): $24.500 (10und) / $43.000 (20und)
  - Pan de Yuca Fit: $24.500 (10und) / $43.000 (20und)
  - Almojábanas Saludables: $28.500 (10und) / $49.500 (20und)

• Arepas Tradicionales (x 5und - Contienen chía y linaza):
  - Plátano Maduro con queso vegano de almendras: $27.000
  - Yuca con queso vegano de almendras: $27.000
  - Plátano Maduro con queso bajo en grasa: $25.000
  - Yuca con queso bajo en grasa: $25.000

• Mini Arepas (x 10und - Contienen chía):
  - Plátano Maduro con queso: $25.000
  - Plátano Maduro Sin Queso (Vegana): $23.000
  - Digestivas de Yuca con queso bajo en grasa: $25.000
  - Digestivas de Yuca Sin Queso (Vegana): $23.000

• Quesos y Charcutería Saludable:
  - Queso Mozzarella de Almendras (500g, 100% vegano): $60.000
  - Salchicha saludable de cerdo premium (x 5und): $26.000

• Yogures Veganos (Base de coco, sin azúcar - Sabores: Frutos Rojos, Frutos Amarillos, Coco Lulada):
  - Presentación 1100ml: $37.000
  - Presentación 250ml: $13.000

• Mermeladas (250g - $19.000):
  - Sabores: Coco piña, Frutos rojos, Frutos amarillos, Lulo con cardamomo.

• Cremas y Untables (250g - Sin azúcar añadida):
  - Mantequilla Ghee: $30.000
  - Crema Choco Almendras: $43.000
  - Crema de Almendras: $43.000
  - Arequipe Oishi sin azúcar adicionada: $36.000

• Suplementos y Bienestar:
  - Suplementos GutMind (60 cápsulas - Cúrcuma con Pimienta Negra, Resveratrol, Ashwagandha): $65.000 (1und) / $100.000 (2und)
  - Vinagres de Manzana Orgánicos (500ml, con la madre sin filtrar): 
    * Con Flor de Jamaica, Jengibre y Canela: $34.000
    * Con Alcachofa y Jengibre: $34.000
    * Con Canela y Sábila: $34.000
    * Vinagre con la Madre tradicional: $25.000
  - Colágeno Hidrolizado con Biotina (Sabores: Natural o Chocolate): $89.000
  - Stevia en gotas (60ml): $14.500

COMBOS Y PROMOCIONES (Siempre Disponibles):
- Combo Sin Dietas y Sin Culpas ($96.000): Arepa de Yuca Fit x5, Fitbonos x10, Mermelada 250g, Yogurt vegano 1100ml.
- Combo Microbiota Feliz ($94.000): Pan de Yuca Fit x10, Yogurt vegano 250ml, Crema de chocoalmendras 250g, Fitbonos x10.
- Kit Panadería Saludable Sin Gluten y Sin Azúcar ($125.000): Fitbonos x20, Almojábanas x20, Pan de Yuca Fit x20.
- Combo Tardeo Caleño Saludable ($110.500): 2x Pan de Yuca Fit x10, 1x Mermelada 250g, 2x Fitbonos x10.
- Combo Intestino Feliz ($65.000): 5x Yogurt vegano 250ml.
- Combo Dulce Sin Azúcar y Sin Culpas ($68.400): 4x Mermeladas 250g.
- Combo Dulce Sin Remordimientos Keto Saludable ($108.500): Crema de chocoalmendras, Crema de almendras, Arequipe Oishi.

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
No le digas al cliente que lo transfieres a un humano. Usa frases naturales como: "Dame un segundito por favor, ya te reviso eso..." o "Permíteme un momento, voy a confirmar...".
Activa el handoff (trigger_handoff = true) en estos casos:
1. Envío de Imágenes/Comprobantes de Pago: Si envían fotos (como el comprobante de transferencia), escala INMEDIATAMENTE para que un humano verifique el pago en Chatwoot.
2. Cotización de Envío Bog/Med con camión refrigerado, o para cotizar exacto un envío a otra ciudad/zona.
3. Ventas al por mayor (superiores a $350.000 COP).
4. Problemas operativos o dudas médicas complejas.
"""


FILE_SENDING_INSTRUCTION = f"""
ENVÍO DE ARCHIVOS CONFIGURADOS:
Puedes pedirle al sistema que envíe archivos guardados junto con tu respuesta.
{catalog_for_prompt(FILE_CATALOG)}

Reglas configuradas por el negocio:
{config.AI_FILE_SENDING_INSTRUCTIONS}

Para enviar, agrega cada elemento a files_to_send con file_id y un caption breve opcional.
El texto de response debe introducir o acompañar naturalmente el archivo. No afirmes que
enviaste un archivo si su ID no aparece en la lista permitida. No uses esta función para
archivos enviados por el cliente ni para comprobantes entrantes.
"""

def process_message_logic(phone: str, text: str, is_image: bool = False) -> BotTurnResult:
    """
    Usa Gemini para procesar el mensaje, entender el contexto y decidir si hace handoff.
    """
    state_record = get_or_create_customer_state(phone)
    if not state_record:
        return BotTurnResult(response="Disculpa, tuvimos un problema técnico. ¿Puedes intentarlo de nuevo?")
        
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
                    system_instruction=SYSTEM_INSTRUCTION + FILE_SENDING_INSTRUCTION,
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

        selected_files = []
        seen_ids = set()
        for requested in ai_data.get("files_to_send") or []:
            file_id = str(requested.get("file_id", "")).strip()
            item: PresavedFile = FILE_CATALOG.get(file_id)
            if not item or file_id in seen_ids:
                continue
            seen_ids.add(file_id)
            selected_files.append({
                "id": item.id,
                "url": item.url,
                "media_type": item.media_type,
                "filename": item.filename,
                "caption": str(requested.get("caption", "")).strip(),
            })

        if response_text:
            save_message_log(phone, "model", response_text)

        if trigger_handoff:
            print(f"[IA HANDOFF TRIGGERED] Razón: {reason}")
            pause_bot_for_handoff(phone, reason)

        return BotTurnResult(response=response_text, files=selected_files)

    except Exception as e:
        import traceback
        print(f"[ERROR GEMINI] Falló la inferencia con Gemini:")
        traceback.print_exc()
        
        if is_image:
            pause_bot_for_handoff(phone, "Envío de imagen (Fallback)")
            return BotTurnResult(response="¡Recibimos tu archivo! Un asesor lo va a revisar en este momento. Por favor espera un momento.")
        return BotTurnResult(response="Disculpa, en este momento estoy teniendo un retraso en procesar tu mensaje. ¿Podrías escribir nuevamente?")
