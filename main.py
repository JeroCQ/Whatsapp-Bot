import requests
from fastapi import FastAPI, Request, HTTPException, Query, BackgroundTasks
from fastapi.responses import PlainTextResponse

# Importamos la configuración validada de forma segura
from config import config

# Importa nuestra nueva lógica al inicio de main.py
from bot import process_message_logic

app = FastAPI()

def send_whatsapp_message(to_number: str, text: str):
    """Sends a text message using the official Meta Cloud API."""
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
        "text": {
            "preview_url": False,
            "body": text
        }
    }
    
    try:
        response = requests.post(url, headers=headers, json=payload)
        response.raise_for_status()
        print(f"Message sent to {to_number}")
    except requests.exceptions.RequestException as e:
        print(f"Error sending message via Meta API: {e}")

def process_whatsapp_message(sender_phone: str, message_body: str, is_image: bool = False):
    """
    Procesador en segundo plano que conecta la Máquina de Estados de bot.py
    """
    print(f"Procesando Mensaje de {sender_phone} | Es Imagen: {is_image}")
    
    # 1. Obtenemos la respuesta calculada por las reglas de negocio
    ai_response = process_message_logic(sender_phone, message_body, is_image)
    
    # 2. Si hay respuesta (si el bot no está pausado), la enviamos a Meta
    if ai_response:
        send_whatsapp_message(sender_phone, ai_response)

@app.get("/webhook")
async def verify_webhook(
    hub_mode: str = Query(None, alias="hub.mode"),
    hub_verify_token: str = Query(None, alias="hub.verify_token"),
    hub_challenge: str = Query(None, alias="hub.challenge")
):
    if hub_mode == "subscribe" and hub_verify_token == config.WA_VERIFY_TOKEN:
        return PlainTextResponse(content=hub_challenge)
    raise HTTPException(status_code=403, detail="Invalid verify token")

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
                            
                            # Detectamos si es Texto
                            if message.get("type") == "text":
                                message_body = message.get("text", {}).get("body")
                                background_tasks.add_task(process_whatsapp_message, sender_phone, message_body, False)
                            
                            # Detectamos si es Imagen (Comprobante de pago)
                            elif message.get("type") == "image":
                                background_tasks.add_task(process_whatsapp_message, sender_phone, "", True)
                                
        return {"status": "success"}
        
    except Exception as e:
        print(f"Webhook processing error: {e}")
        return {"status": "error"}
