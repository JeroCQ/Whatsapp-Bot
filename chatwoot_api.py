import hashlib
import json
import mimetypes
import subprocess

import imageio_ffmpeg
import requests
from config import config
from http_client import MEDIA_TIMEOUT, get, post, put
from provider_errors import ProviderError, provider_error
from webhook_utils import valid_whatsapp_user_id
from chatwoot_media import (
    MediaValidationError, content_type, get_media_asset, media_kind,
    uploaded_attachment, validate_source_media, verify_attachment_asset,
)


def get_base_url():
    return f"{config.CHATWOOT_BASE_URL}/api/v1/accounts/{int(config.CHATWOOT_ACCOUNT_ID)}"


def _checked(response, operation: str, expected=(200,)):
    if response.status_code not in expected:
        raise provider_error("chatwoot", operation, response, (config.CHATWOOT_API_TOKEN, config.WA_TOKEN))
    return response


def get_headers():
    return {
        "api_access_token": config.CHATWOOT_API_TOKEN,
        "Content-Type": "application/json"
    }


def get_multipart_headers():
    """Headers for Chatwoot multipart requests; requests sets Content-Type."""
    return {"api_access_token": config.CHATWOOT_API_TOKEN}


def _scoped_whatsapp_contact_identifier(user_id: str) -> str:
    """Keep opaque identities separate across business and Meta-number deployments."""
    identity = json.dumps(
        [config.BUSINESS_ID, str(config.WA_PHONE_NUMBER_ID), user_id],
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return f"whatsapp-user:{hashlib.sha256(identity.encode('utf-8')).hexdigest()}"


def get_or_create_contact(phone: str, name: str = "Cliente WhatsApp"):
    """Find a phone contact or a scoped opaque WhatsApp identity, then update its name."""
    url = f"{get_base_url()}/contacts"
    opaque_identity = valid_whatsapp_user_id(phone)
    identifier = _scoped_whatsapp_contact_identifier(phone) if opaque_identity else None
    search_term = identifier if opaque_identity else phone
    
    try:
        inbox_id_int = int(config.CHATWOOT_INBOX_ID)
    except ValueError:
        print(f"[CHATWOOT DEBUG] ERROR GRAVE: CHATWOOT_INBOX_ID no es válido.")
        return None

    # 1. Buscar si el contacto ya existe
    search_url = f"{url}/search"
    try:
        search_res = get(search_url, headers=get_headers(), params={"q": search_term}, allow_redirects=False)
        _checked(search_res, "search_contact")
        contacts = search_res.json().get("payload") or []
        # Chatwoot's q search is partial across several fields; an opaque sender
        # must match its complete scoped identifier, never just the first result.
        contact = next(
            (item for item in contacts if isinstance(item, dict) and item.get("identifier") == identifier),
            None,
        ) if opaque_identity else (contacts[0] if contacts else None)
        if contact:
            contact_id = contact["id"]
            current_name = contact.get("name")
            
            # Si encontramos al cliente, y el nuevo nombre no es el genérico, actualizamos Chatwoot
            if name != "Cliente WhatsApp" and current_name != name:
                update_url = f"{url}/{contact_id}"
                _checked(put(update_url, headers=get_headers(), json={"name": name}, allow_redirects=False), "update_contact", (200, 201))
                
            return contact_id
    except (ProviderError, requests.exceptions.RequestException, ValueError, TypeError, AttributeError, KeyError, IndexError) as e:
        print(f"[PROVIDER ERROR] {e}")
        return None

    # 2. Si no existe, crear uno nuevo
    data = {
        "inbox_id": inbox_id_int,
        "name": name,
    }
    if opaque_identity:
        # API inbox contacts accept an identifier without a phone number. Do not
        # turn a BSUID into an invalid +CC.* phone or infer one from its digits.
        # Contract: https://github.com/chatwoot/chatwoot/blob/642205c5c84f76cf6d24ecdd9d627eecdc766092/app/controllers/api/v1/accounts/contacts_controller.rb#L176
        data["identifier"] = identifier
    else:
        data["phone_number"] = f"+{phone}" if not phone.startswith("+") else phone
    
    try:
        res = post(url, headers=get_headers(), json=data, allow_redirects=False)
        _checked(res, "create_contact", (200, 201))
        return res.json()["payload"]["contact"]["id"]
    except (ProviderError, requests.exceptions.RequestException, ValueError, TypeError, AttributeError, KeyError, IndexError) as e:
        print(f"[PROVIDER ERROR] {e}")
    
    return None

def create_conversation(contact_id: int, *, raise_on_error: bool = False):
    """Abre un ticket nuevo para el asesor (Sin enviar mensaje aún)."""
    url = f"{get_base_url()}/conversations"
    data = {
        "inbox_id": int(config.CHATWOOT_INBOX_ID),
        "contact_id": int(contact_id),
        "status": "open"
    }
    # Automatic mode deliberately leaves staffing and eligibility to Chatwoot.
    # Keep a configured assignee as a rollback value without sending it.
    if config.CHATWOOT_ASSIGNMENT_MODE == "fixed":
        data["assignee_id"] = int(config.CHATWOOT_ASSIGNEE_ID)
    
    try:
        res = post(url, headers=get_headers(), json=data, allow_redirects=False)
        _checked(res, "create_conversation", (200, 201))
        payload = res.json()
        conversation_id = payload["id"]
        response_assignee = payload.get("assignee") or payload.get("meta", {}).get("assignee") or {}
        response_assignee_id = response_assignee.get("id") if isinstance(response_assignee, dict) else None
        print(
            f"[CHATWOOT] Conversation {conversation_id} created "
            f"assignment_mode={config.CHATWOOT_ASSIGNMENT_MODE} "
            f"requested_assignee_id={data.get('assignee_id') or 'inbox-policy'} "
            f"response_assignee_id={response_assignee_id or 'not-returned'}"
        )
        return conversation_id
    except (ProviderError, requests.exceptions.RequestException, ValueError, TypeError, AttributeError, KeyError) as e:
        print(f"[PROVIDER ERROR] {e}")
        if raise_on_error:
            raise
    return None


def send_message_to_chatwoot(conversation_id: int, content: str, is_private: bool = False):
    """Envía un mensaje de texto simple al panel de Chatwoot."""
    url = f"{get_base_url()}/conversations/{conversation_id}/messages"
    data = {
        "content": content,
        # Chatwoot reopens resolved/snoozed conversations on incoming messages,
        # including private ones. Internal notes must not impersonate a customer.
        "message_type": "outgoing" if is_private else "incoming",
        "private": is_private       
    }
    try:
        return _checked(post(url, headers=get_headers(), json=data, allow_redirects=False), "send_message", (200, 201))
    except (ProviderError, requests.exceptions.RequestException) as e:
        print(f"[PROVIDER ERROR] {e}")
        return None


def update_message_status(conversation_id: int, message_id: int, status: str, external_error: str = None):
    """Reflect external WhatsApp delivery back into an API-inbox message."""
    if not conversation_id or not message_id or status not in {"delivered", "failed"}:
        return None
    url = f"{get_base_url()}/conversations/{int(conversation_id)}/messages/{int(message_id)}"
    data = {"status": status}
    if external_error and status == "failed":
        data["external_error"] = str(external_error)[:500]
    try:
        response = put(url, headers=get_headers(), json=data, allow_redirects=False)
        return _checked(response, f"update_message_{status}", (200, 201))
    except (ProviderError, requests.exceptions.RequestException) as exc:
        print(f"[CHATWOOT STATUS WARN] Could not mark message_id={message_id} status={status}: {exc}")
        return None


def download_meta_media(media_id: str):
    """Obtiene un archivo temporal de Meta y devuelve sus bytes y MIME type."""
    url = f"https://graph.facebook.com/v20.0/{media_id}"
    headers = {"Authorization": f"Bearer {config.WA_TOKEN}"}
    
    try:
        # 1. Obtener la URL temporal del archivo
        res = get(url, headers=headers)
        res.raise_for_status()
        media_url = res.json().get("url")
        mime_type = res.json().get("mime_type")
        if not media_url:
            print(f"[META DEBUG] Meta no devolvió URL para media_id {media_id}")
            return None, None

        # 2. Descargar los bytes (Meta exige Authorization también en esta URL)
        media_res = get(media_url, headers=headers, timeout=MEDIA_TIMEOUT)
        media_res.raise_for_status()
        return media_res.content, mime_type or media_res.headers.get("Content-Type")
    except Exception as e:
        # Request exception text can contain Meta's signed download URL.
        print(f"[META MEDIA] result=failed reason={type(e).__name__} status={getattr(getattr(e, 'response', None), 'status_code', None)}", flush=True)
    return None, None


def download_meta_image(media_id: str):
    """Obtiene la URL temporal de Meta y descarga los bytes de la imagen."""
    media_bytes, _ = download_meta_media(media_id)
    return media_bytes



def send_media_to_chatwoot(conversation_id: int, content: str, media_bytes: bytes, mime_type: str = "application/octet-stream", filename: str = "archivo", is_private: bool = False):
    """Forward customer media as incoming, and internal attachments as private notes."""
    mime_type = content_type(mime_type) or "application/octet-stream"
    maximum = getattr(config, "CHATWOOT_MAX_ATTACHMENT_BYTES", 25 * 1024 * 1024)
    details = {"conversation_id": conversation_id, "media_kind": media_kind(mime_type),
               "byte_size": len(media_bytes) if isinstance(media_bytes, bytes) else 0,
               "verification_phase": "source"}
    url = f"{get_base_url()}/conversations/{conversation_id}/messages"
    files = {
        "attachments[]": (filename, media_bytes, mime_type or "application/octet-stream")
    }
    data = {
        "content": content or "📎 Archivo del cliente",
        "message_type": "outgoing" if is_private else "incoming",
        "private": "true" if is_private else "false"
    }
    try:
        validate_source_media(media_bytes, mime_type, maximum)
        details["verification_phase"] = "upload"
        response = post(url, headers=get_multipart_headers(), files=files, data=data, timeout=MEDIA_TIMEOUT, allow_redirects=False)
        details["provider_status"] = response.status_code
        if response.status_code not in {200, 201}:
            raise MediaValidationError("upload_http_error")
        message_id, attachment = uploaded_attachment(response, expected_mime=mime_type,
            expected_size=len(media_bytes), account_id=config.CHATWOOT_ACCOUNT_ID, diagnostics=details)
        details.update(message_id=message_id, attachment_id=attachment["id"])
        details["verification_phase"] = "asset"
        verify_attachment_asset(attachment.get("data_url"), media_bytes, mime_type, maximum,
                                fetch=get_media_asset, diagnostics=details)
        print(f"[CHATWOOT MEDIA] {json.dumps({**details, 'result': 'verified', 'reason': 'asset_bytes_verified'}, sort_keys=True)}", flush=True)
        return response
    except MediaValidationError as error:
        reason = error.reason
    except requests.exceptions.RequestException:
        reason = "provider_transport_error"
    print(f"[CHATWOOT MEDIA] {json.dumps({**details, 'result': 'unverified', 'reason': reason}, sort_keys=True)}", flush=True)
    return None


def send_image_to_chatwoot(conversation_id: int, content: str, image_bytes: bytes, is_private: bool = False):
    """Envía un mensaje con imagen adjunta a Chatwoot (Multipart Form-Data)."""
    return send_media_to_chatwoot(conversation_id, content, image_bytes, "image/jpeg", "comprobante.jpg", is_private)


def extension_from_mime(mime_type: str, default: str = ".ogg"):
    if not mime_type:
        return default
    return mimetypes.guess_extension(mime_type.split(";")[0].strip()) or default


def prepare_audio_for_delivery(audio_bytes: bytes, mime_type: str) -> tuple[bytes, str, str]:
    """Return audio in a broadly supported MP3-compatible format.

    WhatsApp voice notes are commonly Ogg/Opus. Chatwoot can store those files,
    while Chatwoot can also produce formats such as WAV that Meta rejects. Transcode
    unsupported input to MP3 with ffmpeg so both delivery directions can use the
    same byte-oriented normalization.
    """
    source_mime = (mime_type or "audio/ogg").split(";", 1)[0].strip().lower()
    compatible = {
        "audio/mpeg": ".mp3",
        "audio/mp3": ".mp3",
        "audio/mp4": ".m4a",
        "audio/x-m4a": ".m4a",
        "audio/aac": ".aac",
    }
    if source_mime in compatible:
        normalized_mime = "audio/mpeg" if source_mime == "audio/mp3" else source_mime
        return audio_bytes, normalized_mime, compatible[source_mime]

    ffmpeg_executable = imageio_ffmpeg.get_ffmpeg_exe()
    converted = subprocess.run(
        [ffmpeg_executable, "-hide_banner", "-loglevel", "error", "-i", "pipe:0",
         "-vn", "-codec:a", "libmp3lame", "-f", "mp3", "pipe:1"],
        input=audio_bytes,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    ).stdout
    if not converted:
        raise ValueError("ffmpeg produced empty audio")
    return converted, "audio/mpeg", ".mp3"


# Backward-compatible alias for integrations/tests that used the original helper.
_prepare_audio_for_chatwoot = prepare_audio_for_delivery


def send_audio_to_chatwoot(conversation_id: int, audio_bytes: bytes, mime_type: str = "audio/ogg",
                           content: str = "🎙️ Nota de voz del cliente", is_private: bool = False, *, transcript: str = None):
    """Convert audio when explicitly requested; never attach an unverified original."""
    source_mime = (mime_type or "audio/ogg").split(";", 1)[0].strip().lower()
    try:
        prepared_bytes, prepared_mime, extension = _prepare_audio_for_chatwoot(audio_bytes, source_mime)
        return send_media_to_chatwoot(
            conversation_id, content, prepared_bytes, prepared_mime,
            f"nota_de_voz{extension}", is_private,
        )
    except (OSError, RuntimeError, subprocess.SubprocessError, ValueError):
        print(f"[CHATWOOT AUDIO] conversation_id={conversation_id} reason=audio_conversion_failed", flush=True)
        notice = "No se adjuntó la nota de voz porque no se pudo verificar su formato."
        if transcript:
            notice += f"\nTranscripción del audio: {transcript}"
        send_message_to_chatwoot(
            conversation_id, notice, is_private=is_private,
        )
        return None
