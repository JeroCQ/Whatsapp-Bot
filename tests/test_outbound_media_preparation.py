"""Outbound photo preparation keeps the Chatwoot original and fits Meta limits."""

import random
import struct
import subprocess
from types import SimpleNamespace
import zlib

import imageio_ffmpeg
import pytest

import chatwoot_media as media


def png_image(width=1, height=1, *, noisy=False):
    def chunk(kind, value):
        return (struct.pack(">I", len(value)) + kind + value
                + struct.pack(">I", zlib.crc32(kind + value)))

    pixels = (random.Random(31).randbytes(width * height * 3) if noisy
              else b"\xff\x00\x00" * width * height)
    stride = width * 3
    rows = b"".join(b"\x00" + pixels[offset:offset + stride]
                    for offset in range(0, len(pixels), stride))
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows, level=1)) + chunk(b"IEND", b""))


def jpeg_dimensions(data):
    offset = 2
    while offset < len(data):
        assert data[offset] == 0xff
        marker = data[offset + 1]
        length = int.from_bytes(data[offset + 2:offset + 4], "big")
        if marker in {0xc0, 0xc1, 0xc2}:
            height, width = struct.unpack(">HH", data[offset + 5:offset + 9])
            return width, height
        offset += 2 + length
    pytest.fail("JPEG has no image dimensions")


def test_supported_small_png_is_byte_for_byte_unchanged(monkeypatch):
    source = png_image()
    monkeypatch.setattr(media.subprocess, "run", lambda *a, **k: pytest.fail("no conversion needed"))
    assert media.prepare_image_for_whatsapp(source, "image/png") == (source, "image/png", ".png")


def test_supported_small_jpeg_normalizes_mime_without_reencoding(monkeypatch):
    source = b"\xff\xd8\xffsynthetic-jpeg"
    monkeypatch.setattr(media.subprocess, "run", lambda *a, **k: pytest.fail("no conversion needed"))
    assert media.prepare_image_for_whatsapp(source, "image/jpg; charset=binary") == (source, "image/jpeg", ".jpg")


def test_real_oversized_png_becomes_decodable_jpeg_with_preserved_aspect_ratio():
    source = png_image(3072, 768, noisy=True)
    assert len(source) > media.WHATSAPP_IMAGE_MAX_BYTES

    prepared, mime, extension = media.prepare_image_for_whatsapp(source, "image/png")

    assert mime == "image/jpeg" and extension == ".jpg"
    assert 0 < len(prepared) <= media.WHATSAPP_IMAGE_MAX_BYTES
    assert source.startswith(b"\x89PNG")
    assert jpeg_dimensions(prepared) == (2048, 512)
    decoded = subprocess.run(
        [imageio_ffmpeg.get_ffmpeg_exe(), "-nostdin", "-hide_banner", "-loglevel", "error",
         "-xerror", "-i", "pipe:0", "-frames:v", "1", "-f", "null", "-"],
        input=prepared, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, check=True, timeout=10,
    )
    assert decoded.returncode == 0


@pytest.mark.parametrize("source,mime,reason", [
    (b"", "image/jpeg", "attachment_image_empty_or_invalid"),
    (None, "image/jpeg", "attachment_image_empty_or_invalid"),
    (b"GIF89a", "image/gif", "attachment_image_format_unsupported"),
    (b"RIFF", "image/webp", "attachment_image_format_unsupported"),
])
def test_invalid_or_unsupported_image_has_specific_safe_failure(source, mime, reason):
    with pytest.raises(media.MediaValidationError, match=f"^{reason}$"):
        media.prepare_image_for_whatsapp(source, mime)


def test_conversion_timeout_is_bounded_and_does_not_leak_decoder_errors(monkeypatch):
    monkeypatch.setattr(media, "WHATSAPP_IMAGE_MAX_BYTES", 1)
    calls = []

    def timeout(args, **kwargs):
        calls.append((args, kwargs))
        raise subprocess.TimeoutExpired(args, kwargs["timeout"], stderr=b"PRIVATE_DECODER_CONTENT")

    monkeypatch.setattr(media.subprocess, "run", timeout)
    with pytest.raises(media.MediaValidationError) as caught:
        media.prepare_image_for_whatsapp(b"oversized", "image/jpeg")

    assert str(caught.value) == "attachment_image_prepare_timeout"
    assert len(calls) == 1
    assert 0 < calls[0][1]["timeout"] <= media.OUTBOUND_IMAGE_PREPARATION_TIMEOUT


def test_conversion_error_does_not_include_decoder_output(monkeypatch):
    monkeypatch.setattr(media, "WHATSAPP_IMAGE_MAX_BYTES", 1)

    def failure(args, **kwargs):
        raise subprocess.CalledProcessError(1, args, stderr=b"PRIVATE_DECODER_CONTENT")

    monkeypatch.setattr(media.subprocess, "run", failure)
    with pytest.raises(media.MediaValidationError, match="^attachment_image_prepare_failed$"):
        media.prepare_image_for_whatsapp(b"oversized", "image/png")


def test_second_smaller_conversion_can_fit_oversized_first_result(monkeypatch):
    monkeypatch.setattr(media, "WHATSAPP_IMAGE_MAX_BYTES", 10)
    results = iter([b"\xff\xd8\xff" + b"x" * 20, b"\xff\xd8\xffshort"])
    calls = []

    def convert(args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(stdout=next(results))

    monkeypatch.setattr(media.subprocess, "run", convert)
    prepared = media.prepare_image_for_whatsapp(b"x" * 11, "image/png")
    assert prepared == (b"\xff\xd8\xffshort", "image/jpeg", ".jpg")
    assert len(calls) == 2
    assert "2048" in calls[0][0][calls[0][0].index("-vf") + 1]
    assert "1280" in calls[1][0][calls[1][0].index("-vf") + 1]
    assert calls[1][1]["timeout"] <= calls[0][1]["timeout"]


def test_conversion_never_returns_over_limit_or_invalid_output(monkeypatch):
    monkeypatch.setattr(media, "WHATSAPP_IMAGE_MAX_BYTES", 10)
    monkeypatch.setattr(media.subprocess, "run", lambda *a, **k: SimpleNamespace(stdout=b"\xff\xd8\xff" + b"x" * 20))
    with pytest.raises(media.MediaValidationError, match="^attachment_image_too_large_after_prepare$"):
        media.prepare_image_for_whatsapp(b"x" * 11, "image/png")
    monkeypatch.setattr(media.subprocess, "run", lambda *a, **k: SimpleNamespace(stdout=b""))
    with pytest.raises(media.MediaValidationError, match="^attachment_image_prepare_invalid_output$"):
        media.prepare_image_for_whatsapp(b"x" * 11, "image/png")
