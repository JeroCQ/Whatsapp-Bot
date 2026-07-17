import requests
from config import config

def get_base_url():
    base = config.CHATWOOT_BASE_URL.rstrip('/')
    return f"{base}/api/v1/accounts/{config.CHATWOOT_ACCOUNT_ID}"

def get_headers():
    return {
        "api_access_token": config.CHATWOOT_API_TOKEN,
        "Content-Type": "application/json"
    }

def get_or_create_contact(phone: str, name: str = "Cliente WhatsApp"):
    """Busca al cliente en Chatwoot, si no existe, lo crea."""
    url = f"{get_base_url()}/contacts"
    
    try:
        inbox_id_int = int(config.CHATWOOT_INBOX_ID)
    except ValueError:
        print(f"[CHATWOOT DEBUG] ERROR GRAVE: CHATWOOT_INBOX_ID no es válido.")
        return None

    data = {
        "inbox_id": inbox_id_int,
        "name": name,
        "phone_number": f"+{phone}" if not phone.startswith("+") else phone
    }
    
    try:
        res = requests.post(url, headers=get_headers(), json=data)
        if res.status_code in [200, 201]:
            return res.json()["payload"]["contact"]["id"]
        
        # Si falla, intentamos buscarlo
        search_url = f"{url}/search?q={phone}"
        search_res = requests.get(search_url, headers=get_headers())
        if search_res.status_code == 200 and search_res.json().get("payload"):
            return search_res.json()["payload"][0]["id"]
            
    except Exception as e:
        print(f"[CHATWOOT DEBUG] Excepción en get_or_create_contact: {e}")
    
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
        res = requests.post(url, headers=get_headers(), json=data)
        if res.status_code == 200:
            return res.json()["id"]
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
        requests.post(url, headers=get_headers(), json=data)
    except Exception as e:
         print(f"[CHATWOOT DEBUG] Excepción enviando mensaje: {e}")

# --- NUEVAS FUNCIONES PARA MANEJAR IMÁGENES ---

def download_meta_image(media_id: str):
    """Obtiene la URL temporal de Meta y descarga los bytes de la imagen."""
    url = f"https://graph.facebook.com/v20.0/{media_id}"
    headers = {"Authorization": f"Bearer {config.WA_TOKEN}"}
    
    try:
        # 1. Obtener la URL del archivo
        res = requests.get(url, headers=headers)
        if res.status_code == 200:
            media_url = res.json().get("url")
            # 2. Descargar los bytes
            img_res = requests.get(media_url, headers=headers)
            if img_res.status_code == 200:
                return img_res.content
    except Exception as e:
        print(f"[META DEBUG] Error descargando imagen {media_id}: {e}")
    return None

def send_image_to_chatwoot(conversation_id: int, content: str, image_bytes: bytes, is_private: bool = False):
    """Envía un mensaje con archivo adjunto a Chatwoot (Multipart Form-Data)."""
    url = f"{get_base_url()}/conversations/{conversation_id}/messages"
    
    # ATENCIÓN: No usamos get_headers() porque requests debe calcular el Content-Type para multipart
    headers = {"api_access_token": config.CHATWOOT_API_TOKEN}
    
    data = {
        "content": content,
        "message_type": "incoming",
        # Chatwoot requiere que los booleanos en form-data se envíen como strings
        "private": "true" if is_private else "false" 
    }
    
    # El campo debe llamarse exactamente 'attachments[]' con los corchetes
    files = {
        "attachments[]": ("comprobante.jpg", image_bytes, "image/jpeg")
    }
    
    try:
        res = requests.post(url, headers=headers, data=data, files=files)
        print(f"[CHATWOOT DEBUG] Respuesta POST Imagen - Status: {res.status_code}")
        return res
    except Exception as e:
        print(f"[CHATWOOT DEBUG] Excepción enviando imagen: {e}")
        return None
def send_audio_to_chatwoot(conversation_id: int, audio_bytes: bytes):
    """Sube un archivo de audio como mensaje entrante visible al asesor humano."""
    url = f"{config.CHATWOOT_API_URL}/api/v1/accounts/{config.CHATWOOT_ACCOUNT_ID}/conversations/{conversation_id}/messages"
    headers = {
        "api_access_token": config.CHATWOOT_ACCESS_TOKEN
    }
    # Forzamos el nombre con extensión .ogg para que el reproductor web de Chatwoot lo reconozca como nota de voz directamente
    files = {
        'attachments[]': ('nota_de_voz.ogg', audio_bytes, 'audio/ogg')
    }
    data = {
        'message_type': 'incoming',
        'private': 'false'
    }
    try:
        response = requests.post(url, headers=headers, files=files, data=data)
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        print(f"Error enviando archivo de audio a Chatwoot: {e}")
