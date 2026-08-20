import hashlib
import json
import os
import time
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import requests
from fastapi import BackgroundTasks, FastAPI, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

import chatwoot_api
from bot import FILE_CATALOG, process_message_logic, transcribe_audio_message
from config import config
from database import (
    claim_webhook_event,
    claim_follow_up,
    get_message_logs,
    get_or_create_customer_state,
    get_phone_by_chatwoot_id,
    mark_webhook_event_processed,
    invalidate_follow_up,
    register_follow_up,
    reset_client_history,
    recover_failed_handoff,
    resume_bot_state,
    save_message_log,
    update_chatwoot_conversation_id,
)
from http_client import MEDIA_TIMEOUT, get, post
from processing_lock import phone_lock
from queue_client import (
    claim_follow_up,
    enqueue,
    enqueue_in,
    follow_up_delay_seconds,
    get_queue_stats,
    invalidate_follow_up,
    queue_enabled,
    register_follow_up,
)
from webhook_utils import chatwoot_event_identity, is_restart_command
from chatwoot_security import chatwoot_scope, verify_chatwoot_signature
from provider_errors import ProviderError, provider_error, sanitize_text
from dashboard_api import router as dashboard_router

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.DASHBOARD_CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "X-Dashboard-API-Key"],
)
app.include_router(dashboard_router)
app.mount("/public", StaticFiles(directory="public"), name="public")

DEPLOYMENT_COMMIT_SHA = os.getenv("RAILWAY_GIT_COMMIT_SHA") or os.getenv("GIT_COMMIT_SHA") or "unknown"
print(f"[BOOT] WhatsApp bot code loaded. Commit: {DEPLOYMENT_COMMIT_SHA}. Scalable queue build: 2026-07-24.2")

WHATSAPP_MEDIA_TYPES = {"audio", "document", "image", "sticker", "video"}
CATALOG_FORMATS = (
    ("pdf", "application/pdf", "document"),
    ("jpg", "image/jpeg", "image"),
    ("jpeg", "image/jpeg", "image"),
    ("png", "image/png", "image"),
    ("webp", "image/webp", "image"),
)


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


@app.get("/health")
async def health_alias():
    """Conventional platform health endpoint."""
    return await health_check()


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
        response = post(url, headers=headers, json=payload)
        if not 200 <= response.status_code < 300:
            raise provider_error("meta", "send_text", response, (config.WA_TOKEN,))
        return True
    except requests.exceptions.RequestException as e:
        raise ProviderError("meta", "send_text", getattr(getattr(e, "response", None), "status_code", None), message="transport error") from e


def send_scheduled_follow_up(phone_number: str, token: str, message: str):
    """Send a follow-up only if no newer customer message invalidated it."""
    if not claim_follow_up(phone_number, token):
        print(f"[FOLLOW UP] Cancelado o reemplazado para {phone_number}")
        return
    send_whatsapp_message(phone_number, message)
    save_message_log(phone_number, "model", message)
    print(f"[FOLLOW UP] Enviado a {phone_number}")


def schedule_follow_up(phone_number: str, message: str, delay_minutes: int):
    """Persist and enqueue the AI-authored follow-up."""
    if not message or not queue_enabled():
        if message:
            print("[FOLLOW UP WARN] REDIS_URL no configurado; no se puede programar el mensaje durablemente")
        return
    try:
        delay_seconds = follow_up_delay_seconds(delay_minutes)
        token = register_follow_up(phone_number, delay_seconds)
        enqueue_in(delay_seconds, send_scheduled_follow_up, phone_number, token, message,
                   job_id=f"follow-up-{phone_number}-{token}")
        print(f"[FOLLOW UP] Programado para {phone_number} en {delay_seconds} segundos")
    except Exception as exc:
        invalidate_follow_up(phone_number)
        # A reminder must never turn an otherwise successful customer reply into
        # a failed/retried webhook (for example, while the SQL migration is pending).
        print(f"[FOLLOW UP WARN] No se pudo programar para {phone_number}: {exc}")


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
        response = post(url, headers=headers, json=payload)
        if not 200 <= response.status_code < 300:
            raise provider_error("meta", f"send_{media_type}", response, (config.WA_TOKEN,))
        return True
    except requests.exceptions.RequestException as e:
        raise ProviderError("meta", f"send_{media_type}", getattr(getattr(e, "response", None), "status_code", None), message="transport error") from e



def catalog_link_for_whatsapp(file_id: str, link: str) -> str:
    """Add a cache-busting query to dashboard-managed catalog links sent through Meta."""
    if file_id != "catalogo_pdf":
        return link
    try:
        response = requests.head(link, timeout=MEDIA_TIMEOUT, allow_redirects=True)
        response.raise_for_status()
        version = response.headers.get("etag") or response.headers.get("last-modified") or response.headers.get("content-length")
    except requests.exceptions.RequestException as exc:
        print(f"[FILE CATALOG] Could not version catalog link for WhatsApp cache busting: {exc}")
        version = str(int(time.time()))
    parts = urlsplit(link)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query["v"] = str(version or int(time.time()))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def current_catalog_for_whatsapp() -> tuple[str, str, str, str] | None:
    """Find this deployment's active PDF/image catalog from public Storage metadata."""
    for extension, expected_content_type, media_type in CATALOG_FORMATS:
        link = config.catalog_public_url(extension=extension)
        try:
            response = requests.head(link, timeout=MEDIA_TIMEOUT, allow_redirects=True)
        except requests.exceptions.RequestException:
            continue
        if not 200 <= response.status_code < 300:
            continue
        content_type = (response.headers.get("Content-Type") or expected_content_type).split(";", 1)[0].lower()
        if content_type != expected_content_type:
            print("[FILE CATALOG] Stored catalog has an unexpected content type")
            return None
        return link, content_type, media_type, extension
    return None



def upload_public_url_to_meta_media(file_url: str, filename: str, fallback_mime_type: str = "application/pdf") -> str:
    """Download a public file and upload it to Meta so WhatsApp receives fresh media bytes."""
    try:
        res = get(file_url, timeout=MEDIA_TIMEOUT)
        res.raise_for_status()
        mime_type = (res.headers.get("Content-Type") or fallback_mime_type).split(";")[0]
        url = f"https://graph.facebook.com/v20.0/{config.WA_PHONE_NUMBER_ID}/media"
        headers = {"Authorization": f"Bearer {config.WA_TOKEN}"}
        files = {"file": (filename, res.content, mime_type)}
        data = {"messaging_product": "whatsapp"}
        response = post(url, headers=headers, files=files, data=data, timeout=MEDIA_TIMEOUT)
        response.raise_for_status()
        media_id = response.json().get("id")
        if media_id:
            print(f"[FILE CATALOG] Uploaded fresh catalog bytes to Meta media_id={media_id}")
        else:
            print(f"[FILE CATALOG] Meta did not return media id for fresh catalog upload: {response.text}")
        return media_id
    except requests.exceptions.RequestException as exc:
        response = getattr(exc, "response", None)
        detail = response.text if response is not None else str(exc)
        print(f"[FILE CATALOG] Error uploading fresh catalog bytes to Meta: {detail}")
        return None


def send_presaved_file(to_number: str, file_id: str):
    """Send one allow-listed file selected by Gemini from the configured catalog."""
    item = FILE_CATALOG.get(file_id)
    if not item:
        print(f"[FILE CATALOG] Ignoring unknown file id requested by AI: {file_id}")
        return
    resolved_filename = item.filename
    media_type = item.media_type
    catalog_content_type = "application/pdf"
    if item.media_id:
        media_reference = {"id": item.media_id}
    else:
        source_link = item.link
        catalog_extension = "pdf"
        if file_id == "catalogo_pdf":
            current_catalog = current_catalog_for_whatsapp()
            if not current_catalog:
                print("[FILE CATALOG] No active PDF/image catalog exists for this deployment")
                return False
            source_link, catalog_content_type, media_type, catalog_extension = current_catalog
        resolved_link = catalog_link_for_whatsapp(file_id, source_link)
        media_reference = {"link": resolved_link}
        if file_id == "catalogo_pdf":
            version = dict(parse_qsl(urlsplit(resolved_link).query, keep_blank_values=True)).get("v", "")
            digest = hashlib.sha256(version.encode("utf-8")).hexdigest()[:12] if version else str(int(time.time()))
            resolved_filename = f"catalogo-{config.BUSINESS_ID}-{digest}.{catalog_extension}"
            print(f"[FILE CATALOG] Uploading current {media_type} catalog to Meta")
            media_id = upload_public_url_to_meta_media(resolved_link, resolved_filename, catalog_content_type)
            if not media_id:
                print("[FILE CATALOG] Catalog delivery stopped because Meta upload failed")
                return False
            media_reference = {"id": media_id}
    if item.default_caption and media_type in {"document", "image", "video"}:
        media_reference["caption"] = item.default_caption
    if resolved_filename and media_type == "document":
        media_reference["filename"] = resolved_filename
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to_number,
        "type": media_type,
        media_type: media_reference,
    }
    url = f"https://graph.facebook.com/v20.0/{config.WA_PHONE_NUMBER_ID}/messages"
    headers = {"Authorization": f"Bearer {config.WA_TOKEN}", "Content-Type": "application/json"}
    try:
        response = post(url, headers=headers, json=payload)
        if not 200 <= response.status_code < 300:
            raise provider_error("meta", "send_catalog", response, (config.WA_TOKEN,))
        print(f"[FILE CATALOG] Sent file id={file_id} to={to_number}")
        return True
    except requests.exceptions.RequestException as exc:
        raise ProviderError("meta", "send_catalog", getattr(getattr(exc, "response", None), "status_code", None), message="transport error") from exc


def upload_chatwoot_attachment_to_meta(attachment_url: str, fallback_mime_type: str = "application/octet-stream", filename: str = "archivo") -> str:
    """Download a Chatwoot attachment and upload it to Meta's temporary media store."""
    try:
        attachment_url = urljoin(config.CHATWOOT_BASE_URL + "/", attachment_url)
        configured = urlsplit(config.CHATWOOT_BASE_URL)
        target = urlsplit(attachment_url)
        if target.username or target.password or target.fragment or target.scheme != "https" or target.netloc != configured.netloc:
            raise ValueError("Chatwoot attachment URL is not on the configured HTTPS origin")
        chatwoot_headers = {"api_access_token": config.CHATWOOT_API_TOKEN}
        current_url = attachment_url
        for _ in range(4):
            # Active Storage commonly redirects a Chatwoot URL to S3 or another
            # object store.  The redirect is expected, but the Chatwoot API token
            # must never be forwarded to that different origin.
            current_target = urlsplit(current_url)
            request_headers = chatwoot_headers if current_target.netloc == configured.netloc else {}
            res = get(current_url, headers=request_headers, timeout=MEDIA_TIMEOUT, allow_redirects=False, stream=True)
            if not 300 <= res.status_code < 400:
                break
            redirected = urljoin(current_url, res.headers.get("Location", ""))
            redirect_target = urlsplit(redirected)
            if (redirect_target.username or redirect_target.password or redirect_target.fragment or
                    redirect_target.scheme != "https" or not redirect_target.netloc):
                raise ValueError("Unsafe Chatwoot attachment redirect rejected")
            res.close()
            current_url = redirected
        else:
            raise ValueError("Too many Chatwoot attachment redirects")
        res.raise_for_status()
        declared_size = int(res.headers.get("Content-Length", "0") or 0)
        if declared_size > config.CHATWOOT_MAX_ATTACHMENT_BYTES:
            res.close()
            raise ValueError("Chatwoot attachment exceeds configured size limit")
        chunks = []
        size = 0
        for chunk in res.iter_content(64 * 1024):
            size += len(chunk)
            if size > config.CHATWOOT_MAX_ATTACHMENT_BYTES:
                res.close()
                raise ValueError("Chatwoot attachment exceeds configured size limit")
            chunks.append(chunk)
        media_bytes = b"".join(chunks)
        res.close()
        mime_type = (res.headers.get("Content-Type") or fallback_mime_type or "application/octet-stream").split(";")[0]

        url = f"https://graph.facebook.com/v20.0/{config.WA_PHONE_NUMBER_ID}/media"
        headers = {"Authorization": f"Bearer {config.WA_TOKEN}"}
        files = {"file": (filename, media_bytes, mime_type)}
        data = {"messaging_product": "whatsapp"}
        response = post(url, headers=headers, files=files, data=data, timeout=MEDIA_TIMEOUT)
        print(f"[META DEBUG] Respuesta POST Media ({mime_type}) - Status: {response.status_code}")
        response.raise_for_status()
        media_id = response.json().get("id")
        if not media_id:
            raise ProviderError("meta", "upload_chatwoot_attachment", response.status_code, message="missing media id")
        return media_id
    except requests.exceptions.RequestException as e:
        response = getattr(e, "response", None)
        print(f"[PROVIDER ERROR] provider=chatwoot operation=download_attachment status={getattr(response, 'status_code', None)} message=transport_error")
        return None
    except Exception as e:
        print(f"[PROVIDER ERROR] provider=chatwoot operation=download_attachment status=none message={sanitize_text(e, (config.CHATWOOT_API_TOKEN, config.WA_TOKEN))}")
        return None


def _create_handoff_ticket_if_needed(sender_phone: str, sender_name: str, new_state: dict, effective_media_id: str, message_body: str, mime_type: str, filename: str):
    if not new_state["is_paused"] or new_state.get("chatwoot_conversation_id"):
        return

    print("[DEBUG] 8. Bot decidió pausarse, creando ticket...")
    print(
        f"[CHATWOOT CONFIG] account_id={config.CHATWOOT_ACCOUNT_ID} "
        f"inbox_id={config.CHATWOOT_INBOX_ID} assignee_id={config.CHATWOOT_ASSIGNEE_ID}"
    )
    state_check = get_or_create_customer_state(sender_phone)
    if state_check.get("chatwoot_conversation_id"):
        return

    display_name = f"{sender_name} (+{sender_phone})"
    contact_id = chatwoot_api.get_or_create_contact(sender_phone, name=display_name)
    if not contact_id:
        recover_failed_handoff(sender_phone)
        return

    conv_id = chatwoot_api.create_conversation(contact_id)
    if not conv_id:
        recover_failed_handoff(sender_phone)
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
            chatwoot_api.send_media_to_chatwoot(conv_id, short_alert, file_bytes, final_mime_type, filename or f"archivo_cliente{extension}", is_private=False)
        else:
            chatwoot_api.send_message_to_chatwoot(conv_id, short_alert + " *(Error descargando el adjunto)*", is_private=False)
    else:
        chatwoot_api.send_message_to_chatwoot(conv_id, context_details, is_private=True)
        chatwoot_api.send_message_to_chatwoot(conv_id, short_alert, is_private=False)


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
    # Any inbound customer activity cancels the previously planned reminder.
    invalidate_follow_up(sender_phone)

    if is_restart_command(message_body):
        reset_client_history(sender_phone)
        send_whatsapp_message(sender_phone, "🔄 Conversación reiniciada. El bot está activo y empezamos de cero.")
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
    ai_turn = process_message_logic(sender_phone, message_body, is_image)
    print("[DEBUG] 6. Respuesta IA generada")

    if ai_turn:
        primary_delivered = not bool(ai_turn.response)
        if ai_turn.send_files_before_response:
            for file_id in ai_turn.requested_files:
                send_presaved_file(sender_phone, file_id)
        if ai_turn.response:
            send_whatsapp_message(sender_phone, ai_turn.response)
            primary_delivered = True
        if not ai_turn.send_files_before_response:
            for file_id in ai_turn.requested_files:
                send_presaved_file(sender_phone, file_id)
        if ai_turn.requested_files:
            save_message_log(sender_phone, "system", f"Archivos enviados: {', '.join(ai_turn.requested_files)}")
        new_state = get_or_create_customer_state(sender_phone)
        _create_handoff_ticket_if_needed(sender_phone, sender_name, new_state, effective_media_id, message_body, mime_type, filename)
        if primary_delivered and not new_state.get("is_paused"):
            schedule_follow_up(sender_phone, ai_turn.follow_up_message, ai_turn.follow_up_delay_minutes)


def process_chatwoot_event(data: dict, event_id: str = None):
    """Process a Chatwoot webhook event from the queue/worker."""
    started_at = time.perf_counter()
    try:
        account_id, inbox_id = chatwoot_scope(data)
        if account_id != str(config.CHATWOOT_ACCOUNT_ID) or inbox_id != str(config.CHATWOOT_INBOX_ID):
            raise ValueError("Chatwoot event scope does not match this deployment")
        event = data.get("event")
        conversation = data.get("conversation") if isinstance(data.get("conversation"), dict) else {}
        event_status = data.get("status") or conversation.get("status")
        event_conv_id = conversation.get("id") or data.get("id")
        print(
            f"[CHATWOOT EVENT] event={event} conversation_id={event_conv_id} "
            f"status={event_status} account_id={account_id} inbox_id={inbox_id}"
        )
        if event == "message_created" and data.get("message_type") == "outgoing" and not data.get("private", False):
            conv_id = event_conv_id
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
                        if not meta_media_id:
                            raise ProviderError("chatwoot", "forward_attachment", None, message="attachment upload failed")
                        send_whatsapp_media(phone, meta_media_id, whatsapp_media_type, content, attachment_filename)
                if content and not attachments:
                    send_whatsapp_message(phone, content)
        elif event == "conversation_status_changed" and event_status == "resolved":
            conv_id = event_conv_id
            phone = resume_bot_state(conv_id) if conv_id else None
            if phone:
                save_message_log(phone, "system", "RESOLVED: Conversación cerrada por el asesor.")
                send_whatsapp_message(phone, "✅ Tu solicitud ha sido resuelta. Si necesitas algo más, envíame un mensaje.")
                print(f"[CHATWOOT EVENT] Bot resumed phone={phone} conversation_id={conv_id}")
            else:
                print(f"[CHATWOOT EVENT WARN] No paused customer found for resolved conversation_id={conv_id}")
        duration_ms = int((time.perf_counter() - started_at) * 1000)
        print(f"[METRIC] chatwoot_event_processed event_id={event_id} event={event} duration_ms={duration_ms}")
        mark_webhook_event_processed("chatwoot", event_id)
    except Exception as e:
        import traceback
        print("[ERROR CRÍTICO] Falló process_chatwoot_event:")
        traceback.print_exc()
        mark_webhook_event_processed("chatwoot", event_id, status="failed", error=str(e))
        # Let RQ apply its failure/retry policy instead of reporting a dropped
        # attachment as a successfully completed job.
        if isinstance(e, ProviderError):
            raise


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
@app.post("/chatwoo-webhook", include_in_schema=False)
async def chatwoot_webhook(request: Request, background_tasks: BackgroundTasks):
    raw_body = await request.body()
    if not verify_chatwoot_signature(
        raw_body,
        request.headers.get("X-Chatwoot-Timestamp", ""),
        request.headers.get("X-Chatwoot-Signature", ""),
        config.CHATWOOT_WEBHOOK_SECRET or "",
    ):
        raise HTTPException(status_code=401, detail="Invalid Chatwoot webhook signature")
    try:
        data = json.loads(raw_body)
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="Invalid JSON")
    account_id, inbox_id = chatwoot_scope(data)
    if account_id != str(config.CHATWOOT_ACCOUNT_ID) or inbox_id != str(config.CHATWOOT_INBOX_ID):
        raise HTTPException(status_code=403, detail="Chatwoot account or inbox mismatch")
    event_id = chatwoot_event_identity(data)
    conv_id = data.get("conversation", {}).get("id") or data.get("id")
    if not claim_webhook_event("chatwoot", event_id, str(conv_id) if conv_id else None):
        print(f"[WEBHOOK DEBUG] Chatwoot duplicado ignorado: {event_id}")
        return {"status": "success", "duplicate": True}
    mode = _dispatch(background_tasks, process_chatwoot_event, data, event_id=event_id)
    print(
        f"[CHATWOOT WEBHOOK] accepted event={data.get('event')} "
        f"conversation_id={conv_id} event_id={event_id} mode={mode}"
    )
    return {"status": "success", "mode": mode}


@app.post("/")
async def root_webhook_dispatcher(request: Request, background_tasks: BackgroundTasks):
    """Retain only the legacy Meta root callback; Chatwoot has an authenticated route."""
    data = await request.json()
    if data.get("object") == "whatsapp_business_account":
        return await receive_webhook(request, background_tasks)
    if data.get("event"):
        raise HTTPException(status_code=404, detail="Use /chatwoot-webhook")
    print(f"[WEBHOOK DEBUG] POST / payload no reconocido: keys={list(data.keys())}")
    return {"status": "ignored", "reason": "unrecognized_root_webhook_payload"}
