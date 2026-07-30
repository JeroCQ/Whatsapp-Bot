"""Configuration and validation for files the AI may send to customers."""

import json
import mimetypes
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse


SUPPORTED_MEDIA_TYPES = {"audio", "document", "image", "video"}


@dataclass(frozen=True)
class PresavedFile:
    id: str
    url: str
    media_type: str
    filename: Optional[str] = None
    description: str = ""


def _infer_media_type(url: str, mime_type: str = "") -> str:
    mime_type = (mime_type or mimetypes.guess_type(urlparse(url).path)[0] or "").lower()
    for media_type in ("image", "video", "audio"):
        if mime_type.startswith(f"{media_type}/"):
            return media_type
    return "document"


def load_file_catalog(raw_json: Optional[str]) -> dict[str, PresavedFile]:
    """Parse the allowlist supplied in AI_FILES_JSON, ignoring unsafe entries."""
    if not raw_json:
        return {}
    try:
        entries = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        raise ValueError(f"AI_FILES_JSON is not valid JSON: {exc}") from exc
    if not isinstance(entries, list):
        raise ValueError("AI_FILES_JSON must be a JSON array")

    catalog = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        file_id = str(entry.get("id", "")).strip()
        url = str(entry.get("url", "")).strip()
        parsed_url = urlparse(url)
        if not file_id or parsed_url.scheme != "https" or not parsed_url.netloc:
            continue
        media_type = str(entry.get("type", "")).strip().lower()
        if media_type not in SUPPORTED_MEDIA_TYPES:
            media_type = _infer_media_type(url, str(entry.get("mime_type", "")))
        catalog[file_id] = PresavedFile(
            id=file_id,
            url=url,
            media_type=media_type,
            filename=str(entry["filename"]).strip() if entry.get("filename") else None,
            description=str(entry.get("description", "")).strip(),
        )
    return catalog


def catalog_for_prompt(catalog: dict[str, PresavedFile]) -> str:
    """Expose only identifiers and human descriptions to the model."""
    if not catalog:
        return "No hay archivos configurados actualmente. Nunca solicites el envío de un archivo."
    lines = ["Archivos permitidos (usa exactamente el ID; nunca inventes IDs):"]
    for item in catalog.values():
        details = item.description or item.filename or item.media_type
        lines.append(f"- ID `{item.id}`: {details}")
    return "\n".join(lines)
