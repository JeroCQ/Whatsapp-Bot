import requests
from fastapi import FastAPI, Request, HTTPException, Query, BackgroundTasks
from fastapi.responses import PlainTextResponse

from config import config
from database import (get_or_create_customer_state, update_chatwoot_conversation_id, 
                      get_phone_by_chatwoot_id, resume_bot_state)
from bot import process_message_logic
import chatwoot_api

app = FastAPI()

def send_whatsapp_message(to_number: str, text: str):
    """Envía un mensaje usando la API de Meta."""
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
        print(f"Error enviando WhatsApp a {to_number}: {e}")

def process_whatsapp_message(sender_phone: str, message_body: str, is_image: bool = False):
    """Procesador que enruta entre el Bot y Chatwoot basado en el estado."""
    state_record = get_or_create_customer_state(sender_phone)
    if not state_record:
        return

    # 1. SI ESTÁ PAUSADO: Reenviar el mensaje del usuario al Asesor en Chatwoot
    if state_record["is_paused"]:
        conv_id = state_record.get("chatwoot_conversation_id")
        if conv_id:
            content = "📸 [El usuario envió un comprobante/imagen]" if is_image else message_body
            chatwoot_api.send_message_to_chatwoot(conv_id, content)
        return

    # 2. SI NO ESTÁ PAUSADO: El bot piensa y responde
    ai_response = process_message_logic(sender_phone, message_body, is_image)
    if ai_response:
        send_whatsapp_message(sender_phone, ai_response)
        
        # 3. SI EL BOT DECIDIÓ PAUSARSE EN ESTE TURNO: Creamos el ticket en Chatwoot
        new_state = get_or_create_customer_state(sender_phone)
        if new_state["is_paused"] and not new_state.get("chatwoot_conversation_id"):
            contact_id = chatwoot_api.get_or_create_contact(sender_phone)
            if contact_id:
                conv_id = chatwoot_api.create_conversation(contact_id, new_state["handoff_reason"])
                if conv_id:
                    update_chatwoot_conversation_id(sender_phone, conv_id)

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
                        for message in value["messages"]:
                            sender_phone = message.get("from")
                            if message.get("type") == "text":
                                background_tasks.add_task(process_whatsapp_message, sender_phone, message.get("text", {}).get("body"), False)
                            elif message.get("type") == "image":
                                background_tasks.add_task(process_whatsapp_message, sender_phone, "", True)
        return {"status": "success"}
    except Exception as e:
        print(f"Error Webhook Meta: {e}")
        return {"status": "error"}

# --- NUEVO: ENDPOINT PARA CHATWOOT (WEBHOOK INVERSO) ---

@app.post("/chatwoot-webhook")
async def chatwoot_webhook(request: Request, background_tasks: BackgroundTasks):
    """Recibe eventos cuando el humano responde o cierra el ticket."""
    data = await request.json()
    event = data.get("event")
    
    # A. El asesor humano envió un mensaje desde Chatwoot
    if event == "message_created" and data.get("message_type") == "outgoing":
        is_private = data.get("private", False)
        # Solo enviamos si NO es una nota privada interna
        if not is_private:
            conv_id = data.get("conversation", {}).get("id")
            content = data.get("content")
            
            if conv_id and content:
                # Buscamos de quién es este ticket en Supabase
                phone = get_phone_by_chatwoot_id(conv_id)
                if phone:
                    send_whatsapp_message(phone, content)
                    
    # B. El asesor humano resolvió (cerró) la conversación
    elif event == "conversation_status_changed" and data.get("status") == "resolved":
        conv_id = data.get("id")
        if conv_id:
            # Quitamos la pausa y devolvemos al usuario al inicio del embudo
            resume_bot_state(conv_id)
            phone = get_phone_by_chatwoot_id(conv_id)
            if phone:
                # Opcional: Avisarle al usuario que se cerró el ticket
                send_whatsapp_message(phone, "✅ Tu solicitud ha sido resuelta. Si necesitas algo más, envíame un mensaje para ver el menú.")

    return {"status": "success"}
