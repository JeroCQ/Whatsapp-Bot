import os

import requests
from fastapi import FastAPI, Request, HTTPException, Query, BackgroundTasks
from fastapi.responses import PlainTextResponse

from config import config
from database import (get_or_create_customer_state, update_chatwoot_conversation_id, 
                      get_phone_by_chatwoot_id, resume_bot_state, get_message_logs)
from bot import process_message_logic, transcribe_audio_message
import chatwoot_api

app = FastAPI()

DEPLOYMENT_COMMIT_SHA = os.getenv("RAILWAY_GIT_COMMIT_SHA") or os.getenv("GIT_COMMIT_SHA") or "unknown"
print(f"[BOOT] WhatsApp bot code loaded. Commit: {DEPLOYMENT_COMMIT_SHA}. Audio understanding build: 2026-07-21.3")


@app.get("/")
async def health_check():
    """Railway/root health endpoint and visible deployment-version check."""
    return {
        "status": "ok",
        "commit": DEPLOYMENT_COMMIT_SHA,
        "audio_understanding_build": "2026-07-21.3",
    }


@app.on_event("startup")
async def log_deployment_version():
    """Log the Railway commit so audio relay fixes can be verified after deploy."""
    print(f"[STARTUP] WhatsApp bot running commit: {DEPLOYMENT_COMMIT_SHA}")


WHATSAPP_MEDIA_TYPES = {"audio", "document", "image", "sticker", "video"}


def _attachment_url(attachment: dict) -> str:
    """Return the best downloadable URL from a Chatwoot attachment payload."""
    return attachment.get("data_url") or attachment.get("download_url") or attachment.get("thumb_url")


def _attachment_filename(attachment: dict, default: str = "archivo") -> str:
    """Return a stable filename for forwarding a Chatwoot attachment to WhatsApp."""
    return (
        attachment.get("file_name")
        or attachment.get("filename")
        or attachment.get("name")
        or default
    )


def normalize_media_type(file_type: str = None, mime_type: str = None, url: str = None) -> str:
    """Map Chatwoot/Meta attachment metadata to a WhatsApp Cloud API media type."""
    file_type = (file_type or "").lower()
    mime_type = (mime_type or "").lower().split(";")[0]
    url = (url or "").lower()

    if file_type in WHATSAPP_MEDIA_TYPES:
        return file_type
    if mime_type.startswith("audio/") or url.endswith((".ogg", ".oga", ".opus", ".mp3", ".m4a", ".aac", ".wav", ".webm")):
        return "audio"
    if mime_type.startswith("image/") or url.endswith((".jpg", ".jpeg", ".png", ".webp")):
        return "image"
    if mime_type.startswith("video/") or url.endswith((".mp4", ".3gp", ".mov", ".m4v")):
        return "video"
    return "document"


def is_audio_attachment(attachment: dict) -> bool:
    """Return True for Chatwoot audio attachments across webhook payload variants."""
    return normalize_media_type(
        attachment.get("file_type"),
        attachment.get("content_type") or attachment.get("mime_type"),
        _attachment_url(attachment),
    ) == "audio"


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

def send_whatsapp_media(to_number: str, media_id: str, media_type: str, caption: str = None, filename: str = None):
    """Send any WhatsApp-supported media type using an uploaded Meta media id."""
    media_type = normalize_media_type(media_type)
    url = f"https://graph.facebook.com/v20.0/{config.WA_PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {config.WA_TOKEN}",
        "Content-Type": "application/json"
    }
    media_payload = {"id": media_id}
    if caption and media_type in {"document", "image", "video"}:
        media_payload["caption"] = caption
    if filename and media_type == "document":
        media_payload["filename"] = filename

    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to_number,
        "type": media_type,
        media_type: media_payload
    }
    try:
        requests.post(url, headers=headers, json=payload).raise_for_status()
    except requests.exceptions.RequestException as e:
        print(f"Error enviando WhatsApp de {media_type} a {to_number}: {e}")


def send_whatsapp_audio(to_number: str, media_id: str):
    """Envía una nota de voz estructurada usando el ID de contenido de Meta."""
    send_whatsapp_media(to_number, media_id, "audio")


def upload_chatwoot_attachment_to_meta(attachment_url: str, fallback_mime_type: str = "application/octet-stream", filename: str = "archivo") -> str:
    """Download a Chatwoot attachment and upload it to Meta's temporary media store."""
    try:
        if attachment_url.startswith("/"):
            attachment_url = f"{config.CHATWOOT_BASE_URL.rstrip('/')}{attachment_url}"

        chatwoot_headers = {"api_access_token": config.CHATWOOT_API_TOKEN}
        res = requests.get(attachment_url, headers=chatwoot_headers)
        res.raise_for_status()
        mime_type = (res.headers.get("Content-Type") or fallback_mime_type or "application/octet-stream").split(";")[0]

        url = f"https://graph.facebook.com/v20.0/{config.WA_PHONE_NUMBER_ID}/media"
        headers = {"Authorization": f"Bearer {config.WA_TOKEN}"}
        files = {"file": (filename, res.content, mime_type)}
        data = {"messaging_product": "whatsapp"}
        response = requests.post(url, headers=headers, files=files, data=data)
        print(f"[META DEBUG] Respuesta POST Media ({mime_type}) - Status: {response.status_code}")
        response.raise_for_status()
        media_id = response.json().get("id")
        if not media_id:
            print(f"[META DEBUG] Meta no devolvió media id para adjunto de Chatwoot: {response.text}")
        return media_id
    except requests.exceptions.RequestException as e:
        response = getattr(e, "response", None)
        detail = response.text if response is not None else str(e)
        print(f"Error al subir adjunto transitorio a Meta: {detail}")
        return None
    except Exception as e:
        print(f"Error al subir adjunto transitorio a Meta: {e}")
        return None


def upload_audio_to_meta(audio_url: str) -> str:
    """Descarga un audio desde Chatwoot y lo registra en los servidores de Meta."""
    return upload_chatwoot_attachment_to_meta(audio_url, "audio/ogg", "voice_note.ogg")


def process_whatsapp_message(sender_phone: str, sender_name: str, message_body: str, is_image: bool = False, media_id: str = None, is_audio: bool = False, audio_media_id: str = None, media_type: str = None, mime_type: str = None, filename: str = None):
    """Procesador que enruta entre el Bot y Chatwoot basado en el estado (Soporta cualquier archivo)."""
    effective_media_id = media_id or audio_media_id
    effective_media_type = normalize_media_type(media_type or ("image" if is_image else "audio" if is_audio else None), mime_type) if effective_media_id else None
    is_image = effective_media_type == "image"
    is_audio = effective_media_type == "audio"
    print(f"\n[DEBUG] 1. Recibido mensaje de {sender_phone} (Media: {effective_media_type or 'texto'})")
    
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
                if effective_media_id:
                    file_bytes, downloaded_mime = chatwoot_api.download_meta_media(effective_media_id)
                    final_mime_type = mime_type or downloaded_mime or "application/octet-stream"
                    extension = chatwoot_api.extension_from_mime(final_mime_type, ".bin")
                    final_filename = filename or f"archivo_cliente{extension}"
                    label = "📎 El usuario envió un archivo"
                    if effective_media_type == "image":
                        label = "📸 El usuario envió una imagen"
                    elif effective_media_type == "audio":
                        label = "🎙️ El usuario envió una nota de voz"
                    elif effective_media_type == "video":
                        label = "🎥 El usuario envió un video"
                    texto_chatwoot = f"{label}: {message_body}" if message_body else label
                    if file_bytes:
                        chatwoot_api.send_media_to_chatwoot(conv_id, texto_chatwoot, file_bytes, final_mime_type, final_filename, is_private=False)
                    else:
                        chatwoot_api.send_message_to_chatwoot(conv_id, f"{label} [Error al descargar adjunto]. Texto: {message_body}", is_private=False)
                else:
                    chatwoot_api.send_message_to_chatwoot(conv_id, message_body, is_private=False)
            return

        # 2. SI NO ESTÁ PAUSADO Y ES AUDIO: transcribir la nota de voz y responder por texto
        if is_audio:
            print("[DEBUG] 5. Audio recibido con bot activo. Descargando y transcribiendo...")
            audio_bytes, downloaded_mime_type = chatwoot_api.download_meta_media(effective_media_id) if effective_media_id else (None, None)
            transcript = transcribe_audio_message(audio_bytes, downloaded_mime_type or mime_type or "audio/ogg")
            if not transcript:
                send_whatsapp_message(sender_phone, "Perdón, no pude entender bien la nota de voz. ¿Me la puedes escribir por texto, por favor? 🧀")
                return

            print(f"[DEBUG] 5.1 Transcripción de audio: {transcript}")
            message_body = transcript

        # 3. SI NO ESTÁ PAUSADO: El bot piensa y responde texto con normalidad
        print("[DEBUG] 5. Procesando lógica del bot...")
        ai_response = process_message_logic(sender_phone, message_body, is_image)
        print(f"[DEBUG] 6. Respuesta IA generada")
        
        if ai_response:
            send_whatsapp_message(sender_phone, ai_response)
            
            new_state = get_or_create_customer_state(sender_phone)
            if new_state["is_paused"] and not new_state.get("chatwoot_conversation_id"):
                print("[DEBUG] 8. Bot decidió pausarse, creando ticket...")
            
                # NUEVO: Pasamos el nombre real y el número en el formato "Nombre (+Numero)"
                display_name = f"{sender_name} (+{sender_phone})"
                contact_id = chatwoot_api.get_or_create_contact(sender_phone, name=display_name)
                
                if contact_id:
                    conv_id = chatwoot_api.create_conversation(contact_id)
                    if conv_id:
                        update_chatwoot_conversation_id(sender_phone, conv_id)
                        
                        logs = get_message_logs(sender_phone, limit=6)
                        context_str = "\n".join([f"{'👤' if m['role']=='user' else '🤖'}: {m['content']}" for m in logs])
                        reason = new_state.get("handoff_reason", "Razón no especificada")
                        
                        # 1. Mensaje corto para disparar una notificación limpia en el celular
                        short_alert = f"🔔 Handoff: {reason}"
                        
                        # 2. Mensaje detallado con el contexto para lectura interna del asesor
                        context_details = f"**Resumen de últimos mensajes:**\n{context_str}"

                        if effective_media_id:
                            file_bytes, downloaded_mime = chatwoot_api.download_meta_media(effective_media_id)
                            final_mime_type = mime_type or downloaded_mime or "application/octet-stream"
                            extension = chatwoot_api.extension_from_mime(final_mime_type, ".bin")
                            if file_bytes:
                                # Enviamos el archivo adjunto junto con la alerta corta
                                chatwoot_api.send_media_to_chatwoot(conv_id, short_alert, file_bytes, final_mime_type, filename or f"archivo_cliente{extension}", is_private=True)
                                # Inmediatamente enviamos el historial completo en otra nota
                                chatwoot_api.send_message_to_chatwoot(conv_id, context_details, is_private=True)
                            else:
                                chatwoot_api.send_message_to_chatwoot(conv_id, short_alert + " *(Error descargando el adjunto)*", is_private=True)
                                chatwoot_api.send_message_to_chatwoot(conv_id, context_details, is_private=True)
                        else:
                            # Enviamos primero la alerta corta (esta será la notificación push)
                            chatwoot_api.send_message_to_chatwoot(conv_id, short_alert, is_private=True)
                            # Luego inyectamos el historial completo en el chat
                            chatwoot_api.send_message_to_chatwoot(conv_id, context_details, is_private=True)

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
                    # EXTRAER EL NOMBRE DEL CONTACTO DE META
                      contacts = value.get("contacts", [])
                      sender_name = "Cliente"
                      if contacts:
                          sender_name = contacts[0].get("profile", {}).get("name", "Cliente")
                      for message in value["messages"]:
                          sender_phone = message.get("from")
                          message_type = message.get("type")
                          if message_type == "text":
                              background_tasks.add_task(process_whatsapp_message, sender_phone, sender_name, message.get("text", {}).get("body"), False, None, False, None)
                          elif message_type in WHATSAPP_MEDIA_TYPES:
                              media_payload = message.get(message_type, {})
                              inbound_media_id = media_payload.get("id")
                              caption = media_payload.get("caption", "")
                              inbound_mime_type = media_payload.get("mime_type")
                              inbound_filename = media_payload.get("filename")
                              body = caption or ("[Audio Nota]" if message_type == "audio" else "")
                              background_tasks.add_task(
                                  process_whatsapp_message,
                                  sender_phone,
                                  sender_name,  # SE AGREGA AQUÍ TAMBIÉN
                                  body,
                                  message_type == "image",
                                  inbound_media_id,
                                  message_type == "audio",
                                  inbound_media_id if message_type == "audio" else None,
                                  message_type,
                                  inbound_mime_type,
                                  inbound_filename,
                              )
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
                    # 1. Reenviar cualquier adjunto del asesor a WhatsApp
                    if attachments and len(attachments) > 0:
                        for attachment in attachments:
                            data_url = _attachment_url(attachment)
                            if not data_url:
                                print(f"[CHATWOOT DEBUG] Adjunto sin URL descargable: {attachment}")
                                continue

                            attachment_mime = attachment.get("content_type") or attachment.get("mime_type") or "application/octet-stream"
                            attachment_filename = _attachment_filename(attachment)
                            whatsapp_media_type = normalize_media_type(attachment.get("file_type"), attachment_mime, data_url)
                            print(
                                "[DEBUG] Asesor envió adjunto desde Chatwoot. "
                                f"type={whatsapp_media_type} file_type={attachment.get('file_type')} "
                                f"content_type={attachment_mime}. Procesando..."
                            )
                            meta_media_id = upload_chatwoot_attachment_to_meta(data_url, attachment_mime, attachment_filename)
                            if meta_media_id:
                                send_whatsapp_media(phone, meta_media_id, whatsapp_media_type, content, attachment_filename)
                            else:
                                print("[CHATWOOT DEBUG] No se pudo reenviar el adjunto del asesor a WhatsApp: Meta no devolvió media_id")
                    
                    # 2. Si el mensaje además lleva texto explicativo y no fue usado como caption
                    if content and not attachments:
                        send_whatsapp_message(phone, content)
                    
    elif event == "conversation_status_changed" and data.get("status") == "resolved":
        conv_id = data.get("id")
        if conv_id:
            resume_bot_state(conv_id)
            phone = get_phone_by_chatwoot_id(conv_id)
            if phone:
                send_whatsapp_message(phone, "✅ Tu solicitud ha sido resuelta. Si necesitas algo más, envíame un mensaje.")

    return {"status": "success"}

@app.post("/")
async def root_webhook_dispatcher(request: Request, background_tasks: BackgroundTasks):
    """Accept webhook POSTs sent to root and dispatch them to the proper handler."""
    data = await request.json()

    if data.get("object") == "whatsapp_business_account":
        return await receive_webhook(request, background_tasks)

    if data.get("event"):
        return await chatwoot_webhook(request, background_tasks)

    print(f"[WEBHOOK DEBUG] POST / payload no reconocido: keys={list(data.keys())}")
    return {"status": "ignored", "reason": "unrecognized_root_webhook_payload"}
