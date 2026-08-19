import mimetypes
from dataclasses import dataclass, asdict

import requests
from config import config
from http_client import MEDIA_TIMEOUT, get, post, put


@dataclass
class ChatwootDiagnostic:
    ok: bool
    reason: str
    status_code: int | None = None

    def as_dict(self):
        return asdict(self)


def _response_error(action: str, response) -> str:
    """Return a useful Chatwoot error without ever printing the access token."""
    try:
        detail = response.json()
    except ValueError:
        detail = (response.text or "").strip()[:500]
    return f"{action} falló (HTTP {response.status_code}): {detail}"


def diagnose_connection() -> ChatwootDiagnostic:
    """Verify configuration, token scope, account access and inbox access."""
    required = {
        "CHATWOOT_BASE_URL": config.CHATWOOT_BASE_URL,
        "CHATWOOT_API_TOKEN": config.CHATWOOT_API_TOKEN,
        "CHATWOOT_ACCOUNT_ID": config.CHATWOOT_ACCOUNT_ID,
        "CHATWOOT_INBOX_ID": config.CHATWOOT_INBOX_ID,
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        return ChatwootDiagnostic(False, f"Faltan variables: {', '.join(missing)}")
    try:
        int(config.CHATWOOT_ACCOUNT_ID)
        int(config.CHATWOOT_INBOX_ID)
    except (TypeError, ValueError):
        return ChatwootDiagnostic(False, "CHATWOOT_ACCOUNT_ID y CHATWOOT_INBOX_ID deben ser IDs numéricos")

    try:
        response = get(f"{get_base_url()}/inboxes/{int(config.CHATWOOT_INBOX_ID)}", headers=get_headers())
    except requests.RequestException as exc:
        return ChatwootDiagnostic(False, f"No se pudo conectar con Chatwoot: {exc}")
    if response.status_code in (401, 403):
        return ChatwootDiagnostic(
            False,
            "El token no autoriza la Application API. Un token de Agent Bot no sustituye el token de acceso de un usuario; usa el token del perfil de un agente con acceso a este inbox.",
            response.status_code,
        )
    if response.status_code == 404:
        return ChatwootDiagnostic(False, "La cuenta o el inbox no existe, o no pertenece a la cuenta configurada", 404)
    if not response.ok:
        return ChatwootDiagnostic(False, _response_error("Validación del inbox", response), response.status_code)
    return ChatwootDiagnostic(True, "Conexión correcta: token, cuenta e inbox tienen acceso", response.status_code)


def get_base_url():
    base = config.CHATWOOT_BASE_URL.rstrip('/')
    return f"{base}/api/v1/accounts/{config.CHATWOOT_ACCOUNT_ID}"


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
    except (TypeError, ValueError):
        print(f"[CHATWOOT DEBUG] ERROR GRAVE: CHATWOOT_INBOX_ID no es válido.")
        return None

    # 1. Buscar si el contacto ya existe
    search_url = f"{url}/search?q={phone}"
    try:
        search_res = get(search_url, headers=get_headers())
        if not search_res.ok:
            print(f"[CHATWOOT ERROR] {_response_error('Buscar contacto', search_res)}")
            return None
        if search_res.status_code == 200 and search_res.json().get("payload"):
            contact = search_res.json()["payload"][0]
            contact_id = contact["id"]
            current_name = contact.get("name")
            
            # Si encontramos al cliente, y el nuevo nombre no es el genérico, actualizamos Chatwoot
            if name != "Cliente WhatsApp" and current_name != name:
                update_url = f"{url}/{contact_id}"
                put(update_url, headers=get_headers(), json={"name": name})
                
            return contact_id
    except Exception as e:
         print(f"[CHATWOOT DEBUG] Error buscando contacto: {e}")

    # 2. Si no existe, crear uno nuevo
    data = {
        "inbox_id": inbox_id_int,
        "name": name,
        "phone_number": f"+{phone}" if not phone.startswith("+") else phone
    }
    
    try:
        res = post(url, headers=get_headers(), json=data)
        if res.status_code in [200, 201]:
            return res.json()["payload"]["contact"]["id"]
        print(f"[CHATWOOT ERROR] {_response_error('Crear contacto', res)}")
    except Exception as e:
        print(f"[CHATWOOT DEBUG] Excepción en get_or_create_contact (creando): {e}")
    
    return None

def create_conversation(contact_id: int):
    """Abre un ticket nuevo para el asesor (Sin enviar mensaje aún)."""
    url = f"{get_base_url()}/conversations"
    data = {
        "inbox_id": int(config.CHATWOOT_INBOX_ID),
        "contact_id": int(contact_id),
        "status": "open"
    }
    
    try:
        res = post(url, headers=get_headers(), json=data)
        if res.status_code in (200, 201):
            return res.json()["id"]
        print(f"[CHATWOOT ERROR] {_response_error('Crear conversación', res)}")
    except Exception as e:
        print(f"[CHATWOOT DEBUG] Excepción en create_conversation: {e}")
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
        response = post(url, headers=get_headers(), json=data)
        if not response.ok:
            print(f"[CHATWOOT ERROR] {_response_error('Enviar mensaje', response)}")
            return None
        return response
    except Exception as e:
         print(f"[CHATWOOT DEBUG] Excepción enviando mensaje: {e}")


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
        response = post(url, headers=get_multipart_headers(), files=files, data=data, timeout=MEDIA_TIMEOUT)
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


def send_audio_to_chatwoot(conversation_id: int, audio_bytes: bytes, mime_type: str = "audio/ogg"):
    """Sube un audio de WhatsApp como mensaje entrante visible al asesor humano."""
    extension = extension_from_mime(mime_type)
    return send_media_to_chatwoot(
        conversation_id,
        "🎙️ Nota de voz del cliente",
        audio_bytes,
        mime_type or "audio/ogg",
        f"nota_de_voz{extension}",
        False,
    )
