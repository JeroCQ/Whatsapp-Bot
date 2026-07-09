import os
import requests
from fastapi import FastAPI, Request, HTTPException, Query
from fastapi.responses import PlainTextResponse

app = FastAPI()

# Required credentials from your Meta Developer Portal
META_ACCESS_TOKEN = os.getenv("META_ACCESS_TOKEN")
META_PHONE_NUMBER_ID = os.getenv("META_PHONE_NUMBER_ID")
META_VERIFY_TOKEN = os.getenv("META_VERIFY_TOKEN") # A secure string you invent for the webhook setup

def send_whatsapp_message(to_number: str, text: str):
    """Sends a text message using the official Meta Cloud API."""
    url = f"https://graph.facebook.com/v20.0/{META_PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {META_ACCESS_TOKEN}",
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
        return response.json()
    except requests.exceptions.RequestException as e:
        print(f"Error sending message via Meta API: {e}")
        return None

@app.get("/webhook")
async def verify_webhook(
    hub_mode: str = Query(None, alias="hub.mode"),
    hub_verify_token: str = Query(None, alias="hub.verify_token"),
    hub_challenge: str = Query(None, alias="hub.challenge")
):
    """
    Meta sends a GET request during setup to verify your webhook URL.
    You must return the hub_challenge exactly as received.
    """
    if hub_mode == "subscribe" and hub_verify_token == META_VERIFY_TOKEN:
        return PlainTextResponse(content=hub_challenge)
    
    raise HTTPException(status_code=403, detail="Invalid verify token")

@app.post("/webhook")
async def receive_webhook(request: Request):
    """
    Receives incoming message payloads from WhatsApp.
    """
    data = await request.json()

    try:
        # Meta's webhook payload has multiple layers
        if data.get("object") == "whatsapp_business_account":
            for entry in data.get("entry", []):
                for change in entry.get("changes", []):
                    value = change.get("value", {})
                    
                    # Ensure the event actually contains messages (it could be a status update)
                    if "messages" in value:
                        for message in value["messages"]:
                            if message.get("type") == "text":
                                sender_phone = message.get("from")
                                message_body = message.get("text", {}).get("body")
                                
                                print(f"Message from {sender_phone}: {message_body}")
                                
                                # Pass `message_body` into your LangGraph/AI workflow here
                                ai_response = f"AI Agent Response: {message_body}"
                                
                                # Send the reply back to the user
                                send_whatsapp_message(sender_phone, ai_response)
                                
        # Always return a 200 OK fast. If you take longer than a few seconds, 
        # Meta will assume the webhook failed and retry, causing duplicate messages.
        return {"status": "success"}
        
    except Exception as e:
        print(f"Webhook processing error: {e}")
        return {"status": "error"}
