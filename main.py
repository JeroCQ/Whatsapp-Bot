import os
import time

import requests
from fastapi import BackgroundTasks, FastAPI, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse

import chatwoot_api
from bot import process_message_logic, transcribe_audio_message
from config import config
from database import (
    claim_webhook_event,
    get_message_logs,
    get_or_create_customer_state,
    get_phone_by_chatwoot_id,
    mark_webhook_event_processed,
    reset_client_history,
    resume_bot_state,
    save_message_log,
    update_chatwoot_conversation_id,
)
from http_client import MEDIA_TIMEOUT, get, post
from processing_lock import phone_lock
from queue_client import enqueue, get_queue_stats, queue_enabled

app = FastAPI()

DEPLOYMENT_COMMIT_SHA = os.getenv("RAILWAY_GIT_COMMIT_SHA") or os.getenv("GIT_COMMIT_SHA") or "unknown"
print(f"[BOOT] WhatsApp bot code loaded. Commit: {DEPLOYMENT_COMMIT_SHA}. Scalable queue build: 2026-07-24.2")

WHATSAPP_MEDIA_TYPES = {"audio", "document", "image", "sticker", "video"}


@app.get("/")
async def health_check():
    """Railway/root health endpoint and visible deployment-version check."""
    return {
        "status": "ok",
        "commit": DEPLOYMENT_COMMIT_SHA,
        "queue_enabled": queue_enabled(),
        "queue": get_queue_stats(),
        "scalable_queue_build": "2026-07-24.2",
    }


@app.on_event("startup")
async def log_deployment_version():
    """Log the Railway commit so deployments can be verified."""
    print(f"[STARTUP] WhatsApp bot running commit: {DEPLOYMENT_COMMIT_SHA}. Queue enabled: {queue_enabled()}")


def _attachment_url(attachment: dict) -> str:
    """Return the best downloadable URL from a Chatwoot attachment payload."""
    return attachment.get("data_url") or attachment.get("download_url") or attachment.get("thumb_url")


def _attachment_filename(attachment: dict, default: str = "archivo") -> str:
    """Return a stable filename for forwarding a Chatwoot attachment to WhatsApp."""
    return attachment.get("file_name") or attachment.get("filename") or attachment.get("name") or default


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


def send_whatsapp_message(to_number: str, text: str):
    """Envía un mensaje de texto usando la API de Meta."""
    url = f"https://graph.facebook.com/v20.0/{config.WA_PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {config.WA_TOKEN}",
        "Content-Type": "application/json",
    }
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to_number,
        "type": "text",
        "text": {"preview_url": False, "body": text},
    }
    try:
        post(url, headers=headers, json=payload).raise_for_status()
    except requests.exceptions.RequestException as e:
        print(f"Error enviando WhatsApp de texto a {to_number}: {e}")


def send_whatsapp_media(to_number: str, media_id: str, media_type: str, caption: str = None, filename: str = None):
    """Send any WhatsApp-supported media type using an uploaded Meta media id."""
    media_type = normalize_media_type(media_type)
    url = f"https://graph.facebook.com/v20.0/{config.WA_PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {config.WA_TOKEN}",
        "Content-Type": "application/json",
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
        media_type: media_payload,
    }
    try:
        post(url, headers=headers, json=payload).raise_for_status()
    except requests.exceptions.RequestException as e:
        print(f"Error enviando WhatsApp de {media_type} a {to_number}: {e}")


def upload_chatwoot_attachment_to_meta(attachment_url: str, fallback_mime_type: str = "application/octet-stream", filename: str = "archivo") -> str:
    """Download a Chatwoot attachment and upload it to Meta's temporary media store."""
    try:
        if attachment_url.startswith("/"):
            attachment_url = f"{config.CHATWOOT_BASE_URL.rstrip('/')}{attachment_url}"

        chatwoot_headers = {"api_access_token": config.CHATWOOT_API_TOKEN}
        res = get(attachment_url, headers=chatwoot_headers, timeout=MEDIA_TIMEOUT)
        res.raise_for_status()
        mime_type = (res.headers.get("Content-Type") or fallback_mime_type or "application/octet-stream").split(";")[0]

        url = f"https://graph.facebook.com/v20.0/{config.WA_PHONE_NUMBER_ID}/media"
        headers = {"Authorization": f"Bearer {config.WA_TOKEN}"}
        files = {"file": (filename, res.content, mime_type)}
        data = {"messaging_product": "whatsapp"}
        response = post(url, headers=headers, files=files, data=data, timeout=MEDIA_TIMEOUT)
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


def _create_handoff_ticket_if_needed(sender_phone: str, sender_name: str, new_state: dict, effective_media_id: str, message_body: str, mime_type: str, filename: str):
    if not new_state["is_paused"] or new_state.get("chatwoot_conversation_id"):
        return

    print("[DEBUG] 8. Bot decidió pausarse, creando ticket...")
    state_check = get_or_create_customer_state(sender_phone)
    if state_check.get("chatwoot_conversation_id"):
        return

    display_name = f"{sender_name} (+{sender_phone})"
    contact_id = chatwoot_api.get_or_create_contact(sender_phone, name=display_name)
    if not contact_id:
        return

    conv_id = chatwoot_api.create_conversation(contact_id)
    if not conv_id:
        return

    update_chatwoot_conversation_id(sender_phone, conv_id)
    logs = get_message_logs(sender_phone, limit=6)
    context_str = "\n".join([f"{'👤' if m['role']=='user' else '🤖'}: {m['content']}" for m in logs])
    reason = new_state.get("handoff_reason", "Razón no especificada")
    save_message_log(sender_phone, "system", f"HANDOFF: Transferido a humano. Razón: {reason}")
    short_alert = f"🔔 {reason}"
    context_details = f"**Resumen de últimos mensajes:**\n{context_str}"

    if effective_media_id:
        file_bytes, downloaded_mime = chatwoot_api.download_meta_media(effective_media_id)
        final_mime_type = mime_type or downloaded_mime or "application/octet-stream"
        extension = chatwoot_api.extension_from_mime(final_mime_type, ".bin")
        chatwoot_api.send_message_to_chatwoot(conv_id, context_details, is_private=True)
        if file_bytes:
            chatwoot_api.send_media_to_chatwoot(conv_id, short_alert, file_bytes, final_mime_type, filename or f"archivo_cliente{extension}", is_private=True)
        else:
            chatwoot_api.send_message_to_chatwoot(conv_id, short_alert + " *(Error descargando el adjunto)*", is_private=True)
    else:
        chatwoot_api.send_message_to_chatwoot(conv_id, context_details, is_private=True)
        chatwoot_api.send_message_to_chatwoot(conv_id, short_alert, is_private=True)


def process_whatsapp_message(sender_phone: str, sender_name: str, message_body: str, is_image: bool = False, media_id: str = None, is_audio: bool = False, audio_media_id: str = None, media_type: str = None, mime_type: str = None, filename: str = None, event_id: str = None):
    """Procesador que enruta entre el Bot y Chatwoot basado en el estado."""
    started_at = time.perf_counter()
    try:
        with phone_lock(sender_phone):
            _process_whatsapp_message_unlocked(sender_phone, sender_name, message_body, is_image, media_id, is_audio, audio_media_id, media_type, mime_type, filename)
        duration_ms = int((time.perf_counter() - started_at) * 1000)
        print(f"[METRIC] whatsapp_message_processed event_id={event_id} phone={sender_phone} duration_ms={duration_ms}")
        mark_webhook_event_processed("whatsapp", event_id)
    except Exception as e:
        import traceback
        print("\n[ERROR CRÍTICO] Falló process_whatsapp_message:")
        traceback.print_exc()
        mark_webhook_event_processed("whatsapp", event_id, status="failed", error=str(e))


def _process_whatsapp_message_unlocked(sender_phone: str, sender_name: str, message_body: str, is_image: bool = False, media_id: str = None, is_audio: bool = False, audio_media_id: str = None, media_type: str = None, mime_type: str = None, filename: str = None):
    effective_media_id = media_id or audio_media_id
    effective_media_type = normalize_media_type(media_type or ("image" if is_image else "audio" if is_audio else None), mime_type) if effective_media_id else None
    is_image = effective_media_type == "image"
    is_audio = effective_media_type == "audio"
    print(f"\n[DEBUG] 1. Recibido mensaje de {sender_phone} (Media: {effective_media_type or 'texto'})")

    if message_body and message_body.strip().lower() == "/reset":
        reset_client_history(sender_phone)
        send_whatsapp_message(sender_phone, "🔄 Historial borrado. Empezando de cero.")
        return

    state_record = get_or_create_customer_state(sender_phone, sender_name or "Cliente")
    if not state_record:
        print("[DEBUG] 3. ERROR: No se pudo obtener ni crear el state_record")
        return

    if state_record["is_paused"]:
        print("[DEBUG] 4. Bot pausado, derivando mensaje al asesor humano en Chatwoot")
        log_text = f"[Archivo/Imagen] Texto adjunto: '{message_body}'" if effective_media_id else message_body
        save_message_log(sender_phone, "user", log_text)
        conv_id = state_record.get("chatwoot_conversation_id")
        if not conv_id:
            return
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

    if is_audio:
        print("[DEBUG] 5. Audio recibido con bot activo. Descargando y transcribiendo...")
        audio_bytes, downloaded_mime_type = chatwoot_api.download_meta_media(effective_media_id) if effective_media_id else (None, None)
        transcript = transcribe_audio_message(audio_bytes, downloaded_mime_type or mime_type or "audio/ogg")
        if not transcript:
            send_whatsapp_message(sender_phone, "Perdón, no pude entender bien la nota de voz. ¿Me la puedes escribir por texto, por favor? 🧀")
            return
        print(f"[DEBUG] 5.1 Transcripción de audio: {transcript}")
        message_body = transcript

    print("[DEBUG] 5. Procesando lógica del bot...")
    ai_response = process_message_logic(sender_phone, message_body, is_image)
    print("[DEBUG] 6. Respuesta IA generada")

    if ai_response:
        send_whatsapp_message(sender_phone, ai_response)
        new_state = get_or_create_customer_state(sender_phone)
        _create_handoff_ticket_if_needed(sender_phone, sender_name, new_state, effective_media_id, message_body, mime_type, filename)


def process_chatwoot_event(data: dict, event_id: str = None):
    """Process a Chatwoot webhook event from the queue/worker."""
    started_at = time.perf_counter()
    try:
        event = data.get("event")
        if event == "message_created" and data.get("message_type") == "outgoing" and not data.get("private", False):
            conv_id = data.get("conversation", {}).get("id")
            content = data.get("content")
            attachments = data.get("attachments")
            phone = get_phone_by_chatwoot_id(conv_id) if conv_id else None
            if phone:
                save_message_log(phone, "asesor", content or "[Adjunto enviado por asesor]")
                if attachments:
                    for attachment in attachments:
                        data_url = _attachment_url(attachment)
                        if not data_url:
                            print(f"[CHATWOOT DEBUG] Adjunto sin URL descargable: {attachment}")
                            continue
                        attachment_mime = attachment.get("content_type") or attachment.get("mime_type") or "application/octet-stream"
                        attachment_filename = _attachment_filename(attachment)
                        whatsapp_media_type = normalize_media_type(attachment.get("file_type"), attachment_mime, data_url)
                        meta_media_id = upload_chatwoot_attachment_to_meta(data_url, attachment_mime, attachment_filename)
                        if meta_media_id:
                            send_whatsapp_media(phone, meta_media_id, whatsapp_media_type, content, attachment_filename)
                if content and not attachments:
                    send_whatsapp_message(phone, content)
        elif event == "conversation_status_changed" and data.get("status") == "resolved":
            conv_id = data.get("id")
            phone = resume_bot_state(conv_id) if conv_id else None
            if phone:
                save_message_log(phone, "system", "RESOLVED: Conversación cerrada por el asesor.")
                send_whatsapp_message(phone, "✅ Tu solicitud ha sido resuelta. Si necesitas algo más, envíame un mensaje.")
        duration_ms = int((time.perf_counter() - started_at) * 1000)
        print(f"[METRIC] chatwoot_event_processed event_id={event_id} event={event} duration_ms={duration_ms}")
        mark_webhook_event_processed("chatwoot", event_id)
    except Exception as e:
        import traceback
        print("[ERROR CRÍTICO] Falló process_chatwoot_event:")
        traceback.print_exc()
        mark_webhook_event_processed("chatwoot", event_id, status="failed", error=str(e))


def _dispatch(background_tasks: BackgroundTasks, func, *args, event_id: str = None, **kwargs):
    func_kwargs = dict(kwargs)
    if event_id:
        func_kwargs["event_id"] = event_id
    if queue_enabled():
        job_id = f"{func.__name__}-{event_id}" if event_id else None
        try:
            enqueue(func, *args, job_id=job_id, **func_kwargs)
            return "queued"
        except Exception as exc:
            # Do not drop customer messages just because Redis/RQ is misconfigured.
            print(f"[QUEUE ERROR] Failed to enqueue event_id={event_id}; falling back to FastAPI background task: {exc}")
    background_tasks.add_task(func, *args, **func_kwargs)
    return "background_task"


@app.get("/webhook")
async def verify_webhook(
    hub_mode: str = Query(None, alias="hub.mode"),
    hub_verify_token: str = Query(None, alias="hub.verify_token"),
    hub_challenge: str = Query(None, alias="hub.challenge"),
):
    if hub_mode == "subscribe" and hub_verify_token == config.WA_VERIFY_TOKEN:
        return PlainTextResponse(content=hub_challenge)
    raise HTTPException(status_code=403, detail="Invalid token")


@app.post("/webhook")
async def receive_webhook(request: Request, background_tasks: BackgroundTasks):
    data = await request.json()
    enqueued = 0
    try:
        if data.get("object") == "whatsapp_business_account":
            for entry in data.get("entry", []):
                for change in entry.get("changes", []):
                    value = change.get("value", {})
                    if "messages" not in value:
                        continue
                    contacts = value.get("contacts", [])
                    sender_name = contacts[0].get("profile", {}).get("name", "Cliente") if contacts else "Cliente"
                    for message in value["messages"]:
                        sender_phone = message.get("from")
                        message_type = message.get("type")
                        event_id = message.get("id")
                        if not claim_webhook_event("whatsapp", event_id, sender_phone):
                            print(f"[WEBHOOK DEBUG] WhatsApp duplicado ignorado: {event_id}")
                            continue
                        if message_type == "text":
                            body = message.get("text", {}).get("body")
                            _dispatch(background_tasks, process_whatsapp_message, sender_phone, sender_name, body, False, None, False, None, event_id=event_id)
                            enqueued += 1
                        elif message_type in WHATSAPP_MEDIA_TYPES:
                            media_payload = message.get(message_type, {})
                            inbound_media_id = media_payload.get("id")
                            caption = media_payload.get("caption", "")
                            body = caption or ("[Audio Nota]" if message_type == "audio" else "")
                            _dispatch(
                                background_tasks,
                                process_whatsapp_message,
                                sender_phone,
                                sender_name,
                                body,
                                message_type == "image",
                                inbound_media_id,
                                message_type == "audio",
                                inbound_media_id if message_type == "audio" else None,
                                message_type,
                                media_payload.get("mime_type"),
                                media_payload.get("filename"),
                                event_id=event_id,
                            )
                            enqueued += 1
        return {"status": "success", "accepted": enqueued, "queue_enabled": queue_enabled()}
    except Exception as e:
        print(f"Error Webhook Meta: {e}")
        return {"status": "error"}


@app.post("/chatwoot-webhook")
async def chatwoot_webhook(request: Request, background_tasks: BackgroundTasks):
    data = await request.json()
    event_id = str(data.get("id") or data.get("message_id") or data.get("event_id") or data.get("created_at") or "")
    conv_id = data.get("conversation", {}).get("id") or data.get("id")
    if not claim_webhook_event("chatwoot", event_id, str(conv_id) if conv_id else None):
        print(f"[WEBHOOK DEBUG] Chatwoot duplicado ignorado: {event_id}")
        return {"status": "success", "duplicate": True}
    mode = _dispatch(background_tasks, process_chatwoot_event, data, event_id=event_id)
    return {"status": "success", "mode": mode}


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
