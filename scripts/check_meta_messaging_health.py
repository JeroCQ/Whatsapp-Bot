"""Read only the configured sender's Meta messaging health, without logging IDs or credentials."""

import json
import re

import requests

from provider_errors import numeric_provider_code

STATES = {"AVAILABLE", "LIMITED", "BLOCKED"}
ENTITIES = {"PHONE_NUMBER", "WABA", "BUSINESS", "APP"}


def summarize(payload, status):
    error = payload.get("error")
    if isinstance(error, dict):
        return {"http_status": status, "error_code": numeric_provider_code(error.get("code")),
                "error_subcode": numeric_provider_code(error.get("error_subcode"))}

    health = payload.get("health_status")
    if not isinstance(health, dict):
        return {"http_status": status, "result": "unavailable"}
    state = health.get("can_send_message")
    details = []
    nodes = health.get("entities") or []
    for entity in nodes:
        if not isinstance(entity, dict) or entity.get("entity_type") not in ENTITIES:
            continue
        node_state = entity.get("can_send_message")
        errors = entity.get("errors") or []
        codes = {numeric_provider_code(error.get("error_code")) for error in errors if isinstance(error, dict)}
        details.append({"entity": entity["entity_type"],
                        "state": node_state if node_state in STATES else "UNKNOWN",
                        "error_codes": sorted(codes - {None})})
    return {"http_status": status, "state": state if state in STATES else "UNKNOWN", "entities": details}


def main():
    try:
        from config import config

        sender = str(config.WA_PHONE_NUMBER_ID or "")
        if not re.fullmatch(r"[0-9]{5,30}", sender) or not config.WA_TOKEN:
            result = {"result": "unavailable", "reason": "sender_not_configured"}
        else:
            response = requests.get(
                f"https://graph.facebook.com/v20.0/{sender}",
                params={"fields": "health_status"}, headers={"Authorization": f"Bearer {config.WA_TOKEN}"},
                timeout=(4, 8), allow_redirects=False,
            )
            payload = response.json()
            result = summarize(payload, response.status_code) if isinstance(payload, dict) else {"result": "unavailable"}
    except Exception as error:
        result = {"result": "unavailable", "reason": type(error).__name__}
    print("meta_messaging_health " + json.dumps(result, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
