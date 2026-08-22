import mimetypes
import subprocess

import imageio_ffmpeg
import requests
from config import config
from http_client import MEDIA_TIMEOUT, get, post, put
from provider_errors import ProviderError, provider_error


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


def get_or_create_contact(phone: str, name: str = "Cliente WhatsApp"):
    """Busca al cliente en Chatwoot, si no existe, lo crea. Si existe, actualiza su nombre."""
    url = f"{get_base_url()}/contacts"
    
    try:
        inbox_id_int = int(config.CHATWOOT_INBOX_ID)
    except ValueError:
        print(f"[CHATWOOT DEBUG] ERROR GRAVE: CHATWOOT_INBOX_ID no es válido.")
        return None

    # 1. Buscar si el contacto ya existe
    search_url = f"{url}/search"
    try:
        search_res = get(search_url, headers=get_headers(), params={"q": phone}, allow_redirects=False)
        _checked(search_res, "search_contact")
        if search_res.json().get("payload"):
            contact = search_res.json()["payload"][0]
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
        "phone_number": f"+{phone}" if not phone.startswith("+") else phone
    }
    
    try:
        res = post(url, headers=get_headers(), json=data, allow_redirects=False)
        _checked(res, "create_contact", (200, 201))
        return res.json()["payload"]["contact"]["id"]
    except (ProviderError, requests.exceptions.RequestException, ValueError, TypeError, AttributeError, KeyError, IndexError) as e:
        print(f"[PROVIDER ERROR] {e}")
    
    return None

def create_conversation(contact_id: int):
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
    return None


def send_message_to_chatwoot(conversation_id: int, content: str, is_private: bool = False):
    """Envía un mensaje de texto simple al panel de Chatwoot."""
    url = f"{get_base_url()}/conversations/{conversation_id}/messages"
    data = {
        "content": content,
        "message_type": "incoming", 
        "private": is_private       
    }
    try:
        return _checked(post(url, headers=get_headers(), json=data, allow_redirects=False), "send_message", (200, 201))
    except (ProviderError, requests.exceptions.RequestException) as e:
        print(f"[PROVIDER ERROR] {e}")
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
        print(f"[META DEBUG] Error descargando archivo {media_id}: {e}")
    return None, None


def download_meta_image(media_id: str):
    """Obtiene la URL temporal de Meta y descarga los bytes de la imagen."""
    media_bytes, _ = download_meta_media(media_id)
    return media_bytes



def send_media_to_chatwoot(conversation_id: int, content: str, media_bytes: bytes, mime_type: str = "application/octet-stream", filename: str = "archivo", is_private: bool = False):
    """Sube cualquier archivo de WhatsApp como mensaje entrante visible al asesor humano."""
    url = f"{get_base_url()}/conversations/{conversation_id}/messages"
    files = {
        "attachments[]": (filename, media_bytes, mime_type or "application/octet-stream")
    }
    data = {
        "content": content or "📎 Archivo del cliente",
        "message_type": "incoming",
        "private": "true" if is_private else "false"
    }
    try:
        response = post(url, headers=get_multipart_headers(), files=files, data=data, timeout=MEDIA_TIMEOUT, allow_redirects=False)
        print(f"[CHATWOOT DEBUG] Respuesta POST Archivo - Status: {response.status_code}")
        response.raise_for_status()
        return response
    except requests.exceptions.RequestException as e:
        print(f"[CHATWOOT DEBUG] Error enviando archivo a Chatwoot: {e}")
        return None


def send_image_to_chatwoot(conversation_id: int, content: str, image_bytes: bytes, is_private: bool = False):
    """Envía un mensaje con imagen adjunta a Chatwoot (Multipart Form-Data)."""
    return send_media_to_chatwoot(conversation_id, content, image_bytes, "image/jpeg", "comprobante.jpg", is_private)


def extension_from_mime(mime_type: str, default: str = ".ogg"):
    if not mime_type:
        return default
    return mimetypes.guess_extension(mime_type.split(";")[0].strip()) or default


def _prepare_audio_for_chatwoot(audio_bytes: bytes, mime_type: str) -> tuple[bytes, str, str]:
    """Return audio in a format browsers can play reliably.

    WhatsApp voice notes are commonly Ogg/Opus. Chatwoot can store those files,
    but browser playback support is inconsistent, so transcode unsupported input
    to MP3 with ffmpeg. Keeping this helper byte-oriented also lets callers reuse
    media already downloaded for transcription.
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


def send_audio_to_chatwoot(conversation_id: int, audio_bytes: bytes, mime_type: str = "audio/ogg",
                           content: str = "🎙️ Nota de voz del cliente", is_private: bool = False):
    """Prepare and upload a voice note without ever dropping the original audio."""
    source_mime = (mime_type or "audio/ogg").split(";", 1)[0].strip().lower()
    try:
        prepared_bytes, prepared_mime, extension = _prepare_audio_for_chatwoot(audio_bytes, source_mime)
        return send_media_to_chatwoot(
            conversation_id, content, prepared_bytes, prepared_mime,
            f"nota_de_voz{extension}", is_private,
        )
    except (OSError, RuntimeError, subprocess.SubprocessError, ValueError) as exc:
        print(f"[CHATWOOT AUDIO] No se pudo convertir la nota de voz: {exc}")
        extension = extension_from_mime(source_mime, ".ogg")
        response = send_media_to_chatwoot(
            conversation_id, content, audio_bytes, source_mime,
            f"nota_de_voz_original{extension}", is_private,
        )
        send_message_to_chatwoot(
            conversation_id,
            "⚠️ La nota de voz se adjuntó en su formato original. La reproducción en línea puede no estar disponible; descárgala para escucharla.",
            is_private=is_private,
        )
        return response
