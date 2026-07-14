import requests
from config import config

def get_base_url():
    # Aseguramos que no haya slashes duplicados
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
    
    # Payload para crear contacto
    data = {
        "inbox_id": config.CHATWOOT_INBOX_ID,
        "name": name,
        "phone_number": f"+{phone}" if not phone.startswith("+") else phone
    }
    
    try:
        # Chatwoot ignora la creación si el teléfono ya existe, pero la forma más 
        # robusta por API Server es forzar un POST y capturar la data.
        res = requests.post(url, headers=get_headers(), json=data)
        if res.status_code in [200, 201]:
            return res.json()["payload"]["contact"]["id"]
        
        # Si da error (ej. ya existe), lo buscamos
        search_res = requests.get(f"{url}/search?q={phone}", headers=get_headers())
        if search_res.status_code == 200 and search_res.json().get("payload"):
            return search_res.json()["payload"][0]["id"]
            
    except Exception as e:
        print(f"Error gestionando contacto en Chatwoot: {e}")
    return None

def create_conversation(contact_id: int, reason: str):
    """Abre un ticket nuevo para el asesor."""
    url = f"{get_base_url()}/conversations"
    data = {
        "inbox_id": config.CHATWOOT_INBOX_ID,
        "contact_id": contact_id,
        "status": "open"
    }
    try:
        res = requests.post(url, headers=get_headers(), json=data)
        if res.status_code == 200:
            conv_id = res.json()["id"]
            # Dejamos una nota interna para que el asesor sepa por qué se derivó
            send_message_to_chatwoot(conv_id, f"🚨 ALERTA DE BOT: Handoff requerido por: {reason}", is_private=True)
            return conv_id
    except Exception as e:
        print(f"Error creando ticket: {e}")
    return None

def send_message_to_chatwoot(conversation_id: int, content: str, is_private: bool = False):
    """Envía un mensaje del usuario al panel de Chatwoot."""
    url = f"{get_base_url()}/conversations/{conversation_id}/messages"
    data = {
        "content": content,
        "message_type": "incoming", # 'incoming' hace que aparezca a la izquierda (como cliente)
        "private": is_private       # 'private' True hace que sea una nota interna amarilla
    }
    requests.post(url, headers=get_headers(), json=data)
