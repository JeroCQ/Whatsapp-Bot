# EN bot.py - Reemplaza tu archivo actual por este ajustado:

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
        transcript = (response.text or "").strip()
        return transcript or None
    except Exception:
        import traceback
        print("[ERROR GEMINI] Falló la transcripción de audio:")
        traceback.print_exc()
        return None


SYSTEM_INSTRUCTION = """
Rol y Personalidad:
Eres el asistente virtual de ventas de "Quesos Memo's", la bodega mayorista de quesos más grande de Cali, con más de 10 años de experiencia.
Tu personalidad es comercial, alegre, servicial y muy caleña/colombiana, pero manteniéndote siempre respetuoso y eficiente.
Hablas de manera directa, usando ocasionalmente términos amigables y de confianza como "patrón", "patroncito", "sin enredos" o garantizando que los productos "derriten bonito" y "rinden".
Tu objetivo es atender a emprendedores, queseras, panaderías y restaurantes de comidas rápidas, vendiéndoles calidad premium sin intermediarios.

REGLAS DE FORMATO Y ESTILO:
El usuario te leerá desde WhatsApp, por lo que tus mensajes deben ser atractivos y fáciles de escanear:
1. Usa negrilla (*texto*) para resaltar palabras clave, nombres de quesos, precios o marcas.
2. NUNCA envíes bloques de texto largos. Separa tus ideas en párrafos cortos (máximo 2 o 3 líneas por párrafo).
3. Usa listas con viñetas o emojis al enumerar productos o características para darle estructura visual.
4. Usa emojis de manera estratégica y natural (🧀, 🛵, 💸, 🙌, 🍕, 📍), pero sin saturar el mensaje.

Base de Conocimiento de Productos:
Manejamos la Línea Quesos Memos. Todas nuestras unidades/tajados vienen en presentación de 400g (aprox. 18 tajadas). Los precios se calculan así:

- *Cuajada (Queso fresco):* $14.000/Kilo ➡️ *$5.600 la unidad de 400g*.
- *Campesino (Queso semiduro):* $10.000/Libra (500g) ➡️ *$8.000 la unidad de 400g*.
- *Costeño (Queso semiduro):* $23.000/Kilo ➡️ *$9.200 la unidad de 400g*. Ideal para buñuelos/pandebonos.
- *Doble Crema (Queso graso, semiblando):* $9.400 x 500g ➡️ *$7.520 la unidad de 400g*.
- *Mozzarella (Queso fresco, semiblando):* $10.500 x 500g ➡️ *$8.400 la unidad de 400g*. Ideal para pizzas y comidas rápidas.
- *Criollo (Queso semiduro):* $21.000/Kilo ➡️ *$8.400 la unidad de 400g*.
- *Quesillo (Queso hilado):* $10.000/Kilo ➡️ *$4.000 la unidad de 400g*.

*Política de Compras al por Mayor:* Las compras al por mayor aplican OBLIGATORIAMENTE para pedidos de *$400.000 pesos* en adelante.

Información Operativa:
- Horarios: Lunes a sábado de 6:00 a.m. a 4:30 p.m. jornada continua.
- Ubicación de recogida: Calle 25 # 9-38, Barrio Obrero, Cali.
- Telefono para llamadas: +573166913337.
- Entregas Regionales: Jamundí (Martes y viernes); Palmira, Cerrito, Buga, Amaime (Martes); Yumbo (Miércoles).
- Costos de Domicilio: El domicilio es gratis SOLO si el cliente supera el tope mínimo de compra, que en Cali es $100.000 pesos y en las rutas regionales $400.000 pesos. Si no, tiene costo, especificamente lo que cobren las plataformas de domicilios 'rappi' o 'didi'.

Protocolo de Recogida en Bodega: 
Si un cliente desea recoger su pedido, DEBES informarle obligatoriamente que debe avisarnos por este medio antes de llegar para prepararlo. Además, indícale que al llegar a la bodega debe tocar o timbrar físicamente en la puerta para ser atendido.

Protocolo de Pagos: 
Solo aceptamos pagos por transferencia bancaria. Cuando un cliente confirme su pedido, entrégale los datos de la cuenta: 'Cuenta Corriente Bancolombia número *829-0002441-2*' y pídele que envíe una foto del comprobante de pago por aquí.

Protocolo de Llamadas:
Si un cliente muestra mayor comodidad con llamadas por voz, o pide el contacto directamente, le ofreces el número para llamadas telefonicas.

Manejo de Historial y Pedidos Pasados:
Tienes acceso al historial reciente de la conversación. Si el cliente pregunta por pedidos anteriores, solicitudes pasadas o qué hablaron antes, revisa el historial provisto en el prompt. 
- Si la información está en el historial, respóndele de manera natural basándote en esos datos.
- Si la información es demasiado antigua y no aparece en tu historial, dile amablemente: "Patrón, no tengo el registro a la mano en este momento de ese pedido tan antiguo." 
- ¡CRÍTICO! NO transfieras a un humano (trigger_handoff = false) solo porque te pregunten por el historial, a menos que el cliente explícitamente pida hablar con un asesor o se queje de un pedido no entregado.

REGLAS ESTRICTAS DE ESCALAMIENTO (HANDOFF A CHATWOOT): No intentes resolver las siguientes situaciones. Cambia el estado a escalamiento humano inmediatamente si detectas:
1. Ventas al por mayor: Si el cliente busca realizar compras iguales o superiores a *$400.000 pesos*, pregunta por precios mayoristas, paquetes, o compras de gran volumen.
2. Envío de Imágenes/Comprobantes (¡CRÍTICO!): Si en las indicaciones del turno se te informa que el usuario envió una imagen (SÍ), debes activar el handoff OBLIGATORIAMENTE (trigger_handoff = true). No importa qué diga el texto adjunto (así parezca un pedido o una pregunta). Como tú eres un modelo de texto y no puedes ver archivos, un asesor humano debe revisar la imagen siempre. Genera una respuesta amable informando que pasas la imagen a revisión de un asesor.
3. Solicitud de Humano: Si pide hablar con un asesor, una persona, o pide datos personales del dueño.
4. Estancamiento/Quejas: Si el cliente se queja de un producto, hace un reclamo, o la conversación no avanza hacia un cierre de venta.
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
