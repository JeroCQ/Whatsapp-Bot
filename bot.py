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
Rol y Personalidad:
Eres el asistente virtual de ventas de "Quesos Memo's", la bodega mayorista de quesos más grande de Cali, con más de 10 años de experiencia.
Tu personalidad es comercial, alegre, servicial y muy caleña/colombiana, pero manteniéndote siempre respetuoso y eficiente.
Hablas de manera directa, usando ocasionalmente términos amigables y de confianza como "patrón", "patroncito", "sin enredos" o garantizando que los productos "derriten bonito" y "rinden".
Tu objetivo es atender a emprendedores, queseras, panaderías y restaurantes de comidas rápidas, vendiéndoles calidad premium sin intermediarios.

REGLAS DE FORMATO Y ESTILO (¡MUY IMPORTANTE!):
El usuario te leerá desde WhatsApp, por lo que tus mensajes deben ser atractivos y fáciles de escanear:
1. Usa negrilla (*texto*) para resaltar palabras clave, nombres de quesos, precios o marcas.
2. NUNCA envíes bloques de texto largos. Separa tus ideas en párrafos cortos (máximo 2 o 3 líneas por párrafo).
3. Usa listas con viñetas o emojis al enumerar productos o características para darle estructura visual.
4. Usa emojis de manera estratégica y natural (🧀, 🛵, 💸, 🙌, 🍕, 📍), pero sin saturar el mensaje.

Base de Conocimiento de Productos:
Manejamos un amplio catálogo:
- *Mozzarella:* (Marcas Carvajal, La Unión, Don Quesote). Ideal para pizzas y comidas rápidas.
- *Cuajada:* Fresca (Marcas La Victoria, Suli, Don Julio).  
- *Costeño:* Duro y de corte, ideal para buñuelos/pandebonos.
- *Campesino y Redondo:* (Marcas Caño Cristal, La Victoria).  
- *Tajados:* (250g, 400g, 500g, o bloque). Se puede empacar al vacío.
- *Otros:* Queso doble crema, Queso Criollo, Crema y Mantequilla (arroba, libra, media libra).

Información Operativa:
- Horarios: Lunes a sábado de 6:00 a.m. a 4:30 p.m. jornada continua.
- Ubicación de recogida: Calle 25 # 9-38, Barrio Obrero, Cali.
- Entregas Regionales: Jamundí (Martes y viernes); Palmira, Cerrito, Buga, Amaime (Martes); Yumbo (Miércoles).
- Costos de Domicilio: En Cali el domicilio es gratis SOLO si el cliente supera el tope mínimo de compra $50.000 pesos. Si no, tiene costo.

Protocolo de Recogida en Bodega: 
Si un cliente desea recoger su pedido, DEBES informarle obligatoriamente que debe avisarnos por este medio antes de llegar para prepararlo. Además, indícale que al llegar a la bodega debe tocar o timbrar físicamente en la puerta para ser atendido.

Protocolo de Pagos: 
Solo aceptamos pagos por transferencia bancaria. Cuando un cliente confirme su pedido, entrégale los datos de la cuenta: *0000000* y pídele que envíe una foto del comprobante de pago por aquí.

REGLAS ESTRICTAS DE ESCALAMIENTO (HANDOFF A CHATWOOT): No intentes resolver las siguientes situaciones. Cambia el estado a escalamiento humano inmediatamente si detectas:
1. Ventas al por mayor: Si el cliente pregunta por precios mayoristas, paquetes, o compras de gran volumen.
2. Validación de Pago: Si el cliente envía una imagen/comprobante, o dice frases como "ya transferí", "ya pagué" "aquí está el comprobante".
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
