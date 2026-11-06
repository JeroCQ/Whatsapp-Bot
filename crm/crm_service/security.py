import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass, field
from typing import Any

import jwt
from fastapi import HTTPException, Request

from crm_service.settings import Settings


@dataclass(frozen=True)
class Actor:
    profile: str
    businesses: tuple[str, ...]
    aliases: tuple[str, ...] = ()
    attention_queue: str | None = None
    is_admin: bool = False
    login_profile: str = ""
    queue_actors: dict[str, str] = field(default_factory=dict)
    retired_profiles: tuple[str, ...] = ()

    def owns(self, owner: str | None) -> bool:
        return bool(owner and owner in (self.profile, *self.aliases))

    def authorize(self, business: str) -> None:
        if business not in self.businesses:
            raise HTTPException(403, "Business is outside the authenticated scope")


def authenticate(request: Request, settings: Settings) -> Actor:
    if request.headers.get("origin") or request.headers.get("sec-fetch-site"):
        raise HTTPException(403, "Server-to-server API only")
    auth = request.headers.get("authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(401, "Bearer token required")
    try:
        claims = jwt.decode(
            auth[7:],
            settings.jwt_secret,
            algorithms=["HS256"],
            audience="whatsapp-crm",
            issuer="chatty-catalog-compass",
            options={"require": ["iss", "aud", "profile", "allowed_businesses", "iat", "exp"]},
        )
        profile, allowed = claims["profile"], claims["allowed_businesses"]
        if not isinstance(profile, str) or not profile.strip() or len(profile) > 200:
            raise ValueError
        if not isinstance(allowed, list) or not allowed or not all(isinstance(x, str) for x in allowed):
            raise ValueError
        if type(claims["iat"]) is not int or type(claims["exp"]) is not int:
            raise ValueError
        if not 0 < claims["exp"] - claims["iat"] <= settings.jwt_max_lifetime:
            raise ValueError
        aliases, retired = claims.get("actor_aliases", []), claims.get("retired_profiles", [])
        targets = claims.get("queue_actors", {})
        if any(not isinstance(values, list) or len(values) > 8 or any(not isinstance(v, str) or not 1 <= len(v) <= 200 for v in values) for values in (aliases, retired)):
            raise ValueError
        if not isinstance(targets, dict) or any(k not in {"retail", "wholesale"} or not isinstance(v, str) or not 1 <= len(v) <= 200 for k, v in targets.items()):
            raise ValueError
        queue = claims.get("attention_queue")
        if queue not in {None, "retail", "wholesale"} or claims.get("operator_role", "operator") not in {"admin", "operator"}:
            raise ValueError
        return Actor(profile, tuple(sorted(set(allowed) & settings.businesses.keys())), tuple(aliases), queue,
                     claims.get("operator_role") == "admin" or profile == "admin", str(claims.get("login_profile", profile)),
                     targets, tuple(retired))
    except (jwt.PyJWTError, ValueError, TypeError):
        raise HTTPException(401, "Invalid or expired server token") from None


def encode_cursor(data: dict[str, Any], scope: str, settings: Settings) -> str:
    payload = json.dumps(
        {"v": 1, "scope": scope, "exp": int(time.time()) + 900, "data": data},
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    encoded = base64.urlsafe_b64encode(payload).decode().rstrip("=")
    signature = hmac.new(settings.jwt_secret.encode(), encoded.encode(), hashlib.sha256).hexdigest()
    return encoded + "." + signature


def decode_cursor(value: str, scope: str, settings: Settings) -> dict[str, Any]:
    try:
        if len(value) > 8192:
            raise ValueError
        encoded, signature = value.split(".")
        expected = hmac.new(settings.jwt_secret.encode(), encoded.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError
        payload = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
        if payload["scope"] != scope or payload["exp"] < time.time() or payload["v"] != 1:
            raise ValueError
        return dict(payload["data"])
    except (ValueError, KeyError, TypeError):
        raise HTTPException(400, "Invalid, expired or differently scoped cursor") from None
