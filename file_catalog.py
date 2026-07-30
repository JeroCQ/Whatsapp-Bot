"""Configuration and prompt helpers for files the AI may send to customers."""

import json
from dataclasses import dataclass
from typing import Optional


SUPPORTED_MEDIA_TYPES = {"audio", "document", "image", "video"}


@dataclass(frozen=True)
class PresavedFile:
    id: str
    description: str
    media_type: str
    link: Optional[str] = None
    media_id: Optional[str] = None
    filename: Optional[str] = None
    default_caption: Optional[str] = None


def load_file_catalog(raw_json: str) -> dict[str, PresavedFile]:
    """Parse and validate PRESAVED_FILES_JSON, returning files keyed by safe AI id."""
    if not raw_json or not raw_json.strip():
        return {}
    try:
        entries = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        raise ValueError(f"PRESAVED_FILES_JSON is not valid JSON: {exc}") from exc
    if not isinstance(entries, list):
        raise ValueError("PRESAVED_FILES_JSON must be a JSON array")

    result = {}
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise ValueError(f"File entry {index} must be an object")
        file_id = str(entry.get("id", "")).strip()
        description = str(entry.get("description", "")).strip()
        link = str(entry.get("link", "")).strip() or None
        media_id = str(entry.get("media_id", "")).strip() or None
        media_type = str(entry.get("type", "document")).strip().lower()
        if not file_id or not description:
            raise ValueError(f"File entry {index} requires id and description")
        if file_id in result:
            raise ValueError(f"Duplicate presaved file id: {file_id}")
        if bool(link) == bool(media_id):
            raise ValueError(f"File '{file_id}' must define exactly one of link or media_id")
        if link and not link.startswith("https://"):
            raise ValueError(f"File '{file_id}' link must use HTTPS")
        if media_type not in SUPPORTED_MEDIA_TYPES:
            raise ValueError(f"File '{file_id}' has unsupported type '{media_type}'")
        result[file_id] = PresavedFile(
            id=file_id,
            description=description,
            media_type=media_type,
            link=link,
            media_id=media_id,
            filename=str(entry.get("filename", "")).strip() or None,
            default_caption=str(entry.get("caption", "")).strip() or None,
        )
    return result


def catalog_prompt(catalog: dict[str, PresavedFile]) -> str:
    """Expose only the configured file choices and their intended use to Gemini."""
    if not catalog:
        return "No hay archivos preguardados configurados. Devuelve requested_files=[] siempre."
    lines = [
        "ARCHIVOS PREGUARDADOS DISPONIBLES:",
        "Solicita archivos solo por su ID exacto en requested_files. Nunca inventes un ID.",
        "Decide cuándo enviarlos usando estas descripciones y las reglas del prompt:",
    ]
    for item in catalog.values():
        extras = f"; texto predeterminado: {item.default_caption}" if item.default_caption else ""
        lines.append(f"- ID {item.id!r} ({item.media_type}): {item.description}{extras}")
    lines.append("No afirmes que un archivo fue enviado si su ID no aparece arriba.")
    return "\n".join(lines)
