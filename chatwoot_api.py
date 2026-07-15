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
    
    # IMPORTANTE: Forzamos inbox_id a ser int()
    try:
        inbox_id_int = int(config.CHATWOOT_INBOX_ID)
    except ValueError:
        print(f"[CHATWOOT DEBUG] ERROR GRAVE: CHATWOOT_INBOX_ID no es un número válido: {config.CHATWOOT_INBOX_ID}")
        return None

    data = {
        "inbox_id": inbox_id_int,
        "name": name,
        "phone_number": f"+{phone}" if not phone.startswith("+") else phone
    }
    
    print(f"\n[CHATWOOT DEBUG] Intentando CREAR contacto. URL: {url} | Datos: {data}")
    try:
        res = requests.post(url, headers=get_headers(), json=data)
        print(f"[CHATWOOT DEBUG] Respuesta POST Contacto - Status: {res.status_code} | Body: {res.text}")
        
        if res.status_code in [200, 201]:
            return res.json()["payload"]["contact"]["id"]
        
        # Si falla (ej. teléfono ya existe), intentamos buscarlo
        search_url = f"{url}/search?q={phone}"
        print(f"[CHATWOOT DEBUG] Intentando BUSCAR contacto. URL: {search_url}")
        search_res = requests.get(search_url, headers=get_headers())
        print(f"[CHATWOOT DEBUG] Respuesta GET Búsqueda - Status: {search_res.status_code} | Body: {search_res.text}")
        
        if search_res.status_code == 200 and search_res.json().get("payload"):
            return search_res.json()["payload"][0]["id"]
            
    except Exception as e:
        print(f"[CHATWOOT DEBUG] Excepción en get_or_create_contact: {e}")
    
    return None

def create_conversation(contact_id: int, reason: str):
    """Abre un ticket nuevo para el asesor."""
    url = f"{get_base_url()}/conversations"
    data = {
        "inbox_id": int(config.CHATWOOT_INBOX_ID),
        "contact_id": int(contact_id),
        "status": "open"
    }
    
    print(f"\n[CHATWOOT DEBUG] Intentando CREAR CONVERSACIÓN. URL: {url} | Datos: {data}")
    try:
        res = requests.post(url, headers=get_headers(), json=data)
        print(f"[CHATWOOT DEBUG] Respuesta POST Conversación - Status: {res.status_code} | Body: {res.text}")
        
        if res.status_code == 200:
            conv_id = res.json()["id"]
            send_message_to_chatwoot(conv_id, f"🚨 ALERTA DE BOT: Handoff requerido por: {reason}", is_private=True)
            return conv_id
    except Exception as e:
        print(f"[CHATWOOT DEBUG] Excepción en create_conversation: {e}")
    return None

def send_message_to_chatwoot(conversation_id: int, content: str, is_private: bool = False):
    """Envía un mensaje del usuario al panel de Chatwoot."""
    url = f"{get_base_url()}/conversations/{conversation_id}/messages"
    data = {
        "content": content,
        "message_type": "incoming", 
        "private": is_private       
    }
    
    print(f"\n[CHATWOOT DEBUG] Intentando ENVIAR MENSAJE. URL: {url} | Datos: {data}")
    try:
        res = requests.post(url, headers=get_headers(), json=data)
        print(f"[CHATWOOT DEBUG] Respuesta POST Mensaje - Status: {res.status_code} | Body: {res.text}")
    except Exception as e:
         print(f"[CHATWOOT DEBUG] Excepción enviando mensaje: {e}")
