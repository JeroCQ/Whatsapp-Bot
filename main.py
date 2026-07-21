import requests
from fastapi import FastAPI, Request, HTTPException, Query, BackgroundTasks
from fastapi.responses import PlainTextResponse

from config import config
from database import (get_or_create_customer_state, update_chatwoot_conversation_id, 
                      get_phone_by_chatwoot_id, resume_bot_state, get_message_logs)
from bot import process_message_logic
import chatwoot_api

app = FastAPI()

def send_whatsapp_message(to_number: str, text: str):
    """Envía un mensaje de texto usando la API de Meta."""
    url = f"https://graph.facebook.com/v20.0/{config.WA_PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {config.WA_TOKEN}",
        "Content-Type": "application/json"
    }
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to_number,
        "type": "text",
        "text": {"preview_url": False, "body": text}
    }
    try:
        requests.post(url, headers=headers, json=payload).raise_for_status()
    except requests.exceptions.RequestException as e:
        print(f"Error enviando WhatsApp de texto a {to_number}: {e}")

def send_whatsapp_audio(to_number: str, media_id: str):
    """NUEVO: Envía una nota de voz estructurada usando el ID de contenido de Meta."""
    url = f"https://graph.facebook.com/v20.0/{config.WA_PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {config.WA_TOKEN}",
        "Content-Type": "application/json"
    }
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to_number,
        "type": "audio",
        "audio": {"id": media_id}
    }
    try:
        requests.post(url, headers=headers, json=payload).raise_for_status()
    except requests.exceptions.RequestException as e:
        print(f"Error enviando WhatsApp de audio a {to_number}: {e}")

def upload_audio_to_meta(audio_url: str) -> str:
    """Descarga un audio desde Chatwoot y lo registra en los servidores de Meta."""
    try:
        # Chatwoot puede enviar URLs absolutas o rutas relativas en data_url.
        if audio_url.startswith("/"):
            audio_url = f"{config.CHATWOOT_BASE_URL.rstrip('/')}{audio_url}"

        # Descargar el archivo desde Chatwoot. En instalaciones privadas, el token es
        # necesario para que el adjunto no descargue una página HTML de autenticación.
        chatwoot_headers = {"api_access_token": config.CHATWOOT_API_TOKEN}
        res = requests.get(audio_url, headers=chatwoot_headers)
        res.raise_for_status()
        mime_type = res.headers.get("Content-Type", "audio/ogg").split(";")[0]
        if not mime_type.startswith("audio/"):
            mime_type = "audio/ogg"
        
        # Subir a la API de Media de Meta. WhatsApp requiere multipart/form-data.
        url = f"https://graph.facebook.com/v20.0/{config.WA_PHONE_NUMBER_ID}/media"
        headers = {"Authorization": f"Bearer {config.WA_TOKEN}"}
        files = {
            "file": ("voice_note.ogg", res.content, mime_type),
        }
        data = {"messaging_product": "whatsapp"}
        response = requests.post(url, headers=headers, files=files, data=data)
        response.raise_for_status()
        return response.json().get("id")
    except Exception as e:
        print(f"Error al subir audio transitorio a Meta: {e}")
        return None

def process_whatsapp_message(sender_phone: str, message_body: str, is_image: bool = False, media_id: str = None, is_audio: bool = False, audio_media_id: str = None):
    """Procesador que enruta entre el Bot y Chatwoot basado en el estado (Soporta Audios)."""
    print(f"\n[DEBUG] 1. Recibido mensaje de {sender_phone} (Imagen: {is_image} | Audio: {is_audio})")
    
    try:
        state_record = get_or_create_customer_state(sender_phone)
        
        if not state_record:
            print("[DEBUG] 3. ERROR: No se pudo obtener ni crear el state_record")
            return

        # 1. SI ESTÁ PAUSADO: Reenviar todo tipo de mensajes al Asesor en Chatwoot
        if state_record["is_paused"]:
            print("[DEBUG] 4. Bot pausado, derivando mensaje al asesor humano en Chatwoot")
            conv_id = state_record.get("chatwoot_conversation_id")
            if conv_id:
                if is_image and media_id:
                    img_bytes = chatwoot_api.download_meta_image(media_id)
                    texto_chatwoot = f"📸 El usuario envió una imagen: {message_body}" if message_body else "📸 El usuario envió una imagen"
                    if img_bytes:
                        chatwoot_api.send_image_to_chatwoot(conv_id, texto_chatwoot, img_bytes, is_private=False)
                    else:
                        chatwoot_api.send_message_to_chatwoot(conv_id, f"📸 [Error al descargar la imagen] Texto: {message_body}", is_private=False)
                
                elif is_audio and audio_media_id:
                    audio_bytes, mime_type = chatwoot_api.download_meta_media(audio_media_id)
                    if audio_bytes:
                        chatwoot_api.send_audio_to_chatwoot(conv_id, audio_bytes, mime_type or "audio/ogg")
                    else:
                        chatwoot_api.send_message_to_chatwoot(conv_id, "🎙️ [Error de descarga] El cliente envió una nota de voz que no se pudo procesar.", is_private=False)
                
                else:
                    chatwoot_api.send_message_to_chatwoot(conv_id, message_body, is_private=False)
            return

        # 2. SI NO ESTÁ PAUSADO Y ES AUDIO: (Salvaguarda temporal para el Paso 1)
        if is_audio:
            send_whatsapp_message(sender_phone, "¡Hola! Por ahora solo puedo entenderte por texto escrito. Si necesitas que un asesor escuche tu audio, por favor escribe la palabra *Asesor*. 🧀")
            return

        # 3. SI NO ESTÁ PAUSADO: El bot piensa y responde texto con normalidad
        print("[DEBUG] 5. Procesando lógica del bot...")
        ai_response = process_message_logic(sender_phone, message_body, is_image)
        print(f"[DEBUG] 6. Respuesta IA generada")
        
        if ai_response:
            send_whatsapp_message(sender_phone, ai_response)
            
            new_state = get_or_create_customer_state(sender_phone)
            if new_state["is_paused"] and not new_state.get("chatwoot_conversation_id"):
                print("[DEBUG] 8. Bot decidió pausarse, creando ticket...")
                contact_id = chatwoot_api.get_or_create_contact(sender_phone)
                
                if contact_id:
                    conv_id = chatwoot_api.create_conversation(contact_id)
                    if conv_id:
                        update_chatwoot_conversation_id(sender_phone, conv_id)
                        
                        logs = get_message_logs(sender_phone, limit=6)
                        context_str = "\n".join([f"{'👤' if m['role']=='user' else '🤖'}: {m['content']}" for m in logs])
                        reason = new_state.get("handoff_reason", "Razón no especificada")
                        
                        summary = (
                            f"🚨 **ALERTA DE BOT: Handoff requerido**\n"
                            f"**Razón:** {reason}\n\n"
                            f"**Resumen de últimos mensajes:**\n{context_str}"
                        )

                        if is_image and media_id:
                            img_bytes = chatwoot_api.download_meta_image(media_id)
                            if img_bytes:
                                chatwoot_api.send_image_to_chatwoot(conv_id, summary, img_bytes, is_private=True)
                            else:
                                chatwoot_api.send_message_to_chatwoot(conv_id, summary + "\n\n*(Error descargando la imagen)*", is_private=True)
                        else:
                            chatwoot_api.send_message_to_chatwoot(conv_id, summary, is_private=True)

    except Exception as e:
        import traceback
        print(f"\n[ERROR CRÍTICO] Falló process_whatsapp_message:")
        traceback.print_exc()

# --- ENDPOINTS DE META ---

@app.get("/webhook")
async def verify_webhook(
    hub_mode: str = Query(None, alias="hub.mode"),
    hub_verify_token: str = Query(None, alias="hub.verify_token"),
    hub_challenge: str = Query(None, alias="hub.challenge")
):
    if hub_mode == "subscribe" and hub_verify_token == config.WA_VERIFY_TOKEN:
        return PlainTextResponse(content=hub_challenge)
    raise HTTPException(status_code=403, detail="Invalid token")

@app.post("/webhook")
async def receive_webhook(request: Request, background_tasks: BackgroundTasks):
    data = await request.json()
    try:
        if data.get("object") == "whatsapp_business_account":
            for entry in data.get("entry", []):
                for change in entry.get("changes", []):
                    value = change.get("value", {})
                    if "messages" in value:
                        for message in value["messages"]:
                            sender_phone = message.get("from")
                            
                            if message.get("type") == "text":
                                background_tasks.add_task(process_whatsapp_message, sender_phone, message.get("text", {}).get("body"), False, None, False, None)
                            elif message.get("type") == "image":
                                media_id = message.get("image", {}).get("id")
                                caption = message.get("image", {}).get("caption", "") 
                                background_tasks.add_task(process_whatsapp_message, sender_phone, caption, True, media_id, False, None)
                            # MODIFICADO: Capturar los eventos entrantes de tipo audio
                            elif message.get("type") == "audio":
                                audio_id = message.get("audio", {}).get("id")
                                background_tasks.add_task(process_whatsapp_message, sender_phone, "[Audio Nota]", False, None, True, audio_id)
        return {"status": "success"}
    except Exception as e:
        print(f"Error Webhook Meta: {e}")
        return {"status": "error"}

# --- ENDPOINT PARA CHATWOOT (WEBHOOK INVERSO) ---

@app.post("/chatwoot-webhook")
async def chatwoot_webhook(request: Request, background_tasks: BackgroundTasks):
    data = await request.json()
    event = data.get("event")
    
    if event == "message_created" and data.get("message_type") == "outgoing":
        is_private = data.get("private", False)
        if not is_private:
            conv_id = data.get("conversation", {}).get("id")
            content = data.get("content")
            attachments = data.get("attachments") # NUEVO: Interceptar adjuntos salientes del asesor
            
            if conv_id:
                phone = get_phone_by_chatwoot_id(conv_id)
                if phone:
                    # 1. Verificar si el asesor grabó un audio o adjuntó un archivo de sonido
                    if attachments and len(attachments) > 0:
                        for attachment in attachments:
                            file_type = attachment.get("file_type")
                            data_url = attachment.get("data_url")
                            
                            if file_type == "audio" and data_url:
                                print(f"[DEBUG] Asesor envió audio desde Chatwoot. Procesando...")
                                meta_media_id = upload_audio_to_meta(data_url)
                                if meta_media_id:
                                    send_whatsapp_audio(phone, meta_media_id)
                    
                    # 2. Si el mensaje además lleva texto explicativo
                    if content:
                        send_whatsapp_message(phone, content)
                    
    elif event == "conversation_status_changed" and data.get("status") == "resolved":
        conv_id = data.get("id")
        if conv_id:
            resume_bot_state(conv_id)
            phone = get_phone_by_chatwoot_id(conv_id)
            if phone:
                send_whatsapp_message(phone, "✅ Tu solicitud ha sido resuelta. Si necesitas algo más, envíame un mensaje.")

    return {"status": "success"}
