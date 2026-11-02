"""Provider ceilings and routing apply across formats, without decoding files."""

import pytest

from chatwoot_media import MediaValidationError, whatsapp_media_spec


def webp_prefix(animated):
    chunk = b"VP8X" + (10).to_bytes(4, "little") + bytes([2 if animated else 0]) + b"\x00" * 9
    return b"RIFF" + (len(chunk) + 4).to_bytes(4, "little") + b"WEBP" + chunk


@pytest.mark.parametrize("mime,filename,kind,limit", [
    ("image/jpeg", "photo.jpg", "image", 5 * 1024 * 1024),
    ("image/png", "photo.png", "image", 5 * 1024 * 1024),
    ("audio/ogg; codecs=opus", "voice.ogg", "audio", 16 * 1024 * 1024),
    ("video/mp4", "clip.mp4", "video", 16 * 1024 * 1024),
    ("application/pdf", "file.pdf", "document", 100 * 1024 * 1024),
    ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", "file.docx", "document", 100 * 1024 * 1024),
    ("application/vnd.oasis.opendocument.text", "file.odt", "document", 100 * 1024 * 1024),
    ("application/x-future-format", "file.future", "document", 100 * 1024 * 1024),
    ("image/gif", "animation.gif", "document", 100 * 1024 * 1024),
])
def test_shared_provider_limits_and_unknown_format_document_route(mime, filename, kind, limit):
    spec = whatsapp_media_spec(mime, filename, limit)
    assert spec["media_type"] == kind and spec["maximum_bytes"] == limit
    with pytest.raises(MediaValidationError, match="attachment_provider_size_exceeded"):
        whatsapp_media_spec(mime, filename, limit + 1)


@pytest.mark.parametrize("animated,limit", [(False, 100 * 1024), (True, 500 * 1024)])
def test_webp_sticker_uses_container_flags_without_decoding_or_losing_animation(animated, limit):
    prefix = webp_prefix(animated)
    spec = whatsapp_media_spec("image/webp", "sticker.webp", limit, prefix)
    assert spec == {"media_type": "sticker", "mime_type": "image/webp", "maximum_bytes": limit}
    with pytest.raises(MediaValidationError, match="attachment_provider_size_exceeded"):
        whatsapp_media_spec("image/webp", "sticker.webp", limit + 1, prefix)


def test_incomplete_or_unknown_webp_container_defers_decoding_to_provider():
    assert whatsapp_media_spec("image/webp", "sticker.webp", 125032)["media_type"] == "sticker"


@pytest.mark.parametrize("filename,mime", [
    ("portfolio.PDF", "application/pdf"),
    ("sticker.webp", "image/webp"),
    ("sheet.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
])
def test_generic_http_mime_uses_original_filename_metadata(filename, mime):
    assert whatsapp_media_spec("application/octet-stream", filename, 0)["mime_type"] == mime
