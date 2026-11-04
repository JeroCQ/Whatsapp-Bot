"""Real local codecs and synthetic fixtures; never uploads or sends messages."""

from __future__ import annotations

import hashlib
import struct
import subprocess
import zipfile
from pathlib import Path

import imageio_ffmpeg
import pytest

from crm_media_policy import (
    LIMITS,
    MAX_UPLOAD_BYTES,
    PolicyError,
    create_playback_audio,
    inspect_media,
    safe_filename,
    upload_limit,
    validate_caption,
)


def ffmpeg(*arguments: str) -> None:
    subprocess.run(
        [
            imageio_ffmpeg.get_ffmpeg_exe(),
            "-y",
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            *arguments,
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        timeout=20,
    )


@pytest.fixture(scope="module")
def samples(tmp_path_factory):
    folder = tmp_path_factory.mktemp("synthetic-media")
    ffmpeg(
        "-f",
        "lavfi",
        "-i",
        "color=c=blue:s=64x64",
        "-frames:v",
        "1",
        "-threads",
        "1",
        str(folder / "image.png"),
    )
    ffmpeg(
        "-f",
        "lavfi",
        "-i",
        "color=c=blue:s=64x64",
        "-frames:v",
        "1",
        "-threads",
        "1",
        str(folder / "image.jpg"),
    )
    ffmpeg(
        "-f",
        "lavfi",
        "-i",
        "color=c=blue:s=512x512",
        "-frames:v",
        "1",
        "-c:v",
        "libwebp",
        "-threads",
        "1",
        str(folder / "sticker.webp"),
    )
    ffmpeg(
        "-f",
        "lavfi",
        "-i",
        "testsrc=s=512x512:r=2:d=1",
        "-frames:v",
        "2",
        "-c:v",
        "libwebp_anim",
        "-q:v",
        "70",
        "-loop",
        "0",
        "-threads",
        "1",
        str(folder / "animated.webp"),
    )
    for name, codec, extra in (
        ("sound.ogg", "libopus", []),
        ("sound.webm", "libopus", []),
        ("sound.mp3", "libmp3lame", []),
        ("sound.m4a", "aac", []),
        ("sound.aac", "aac", ["-f", "adts"]),
    ):
        ffmpeg(
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=0.15",
            "-c:a",
            codec,
            "-ac",
            "1",
            *extra,
            str(folder / name),
        )
    ffmpeg(
        "-f",
        "lavfi",
        "-i",
        "color=c=blue:s=64x64:r=10:d=0.2",
        "-f",
        "lavfi",
        "-i",
        "sine=duration=0.2",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-shortest",
        str(folder / "video.mp4"),
    )
    ffmpeg(
        "-f",
        "lavfi",
        "-i",
        "color=c=blue:s=64x64:r=10:d=0.2",
        "-c:v",
        "mpeg4",
        str(folder / "bad-video.mp4"),
    )
    (folder / "document.pdf").write_bytes(
        b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\ntrailer\n<< /Root 1 0 R >>\n%%EOF\n"
    )
    (folder / "document.txt").write_text(
        "Documento sintético con caracteres válidos: año, café.", encoding="utf8"
    )
    return folder


@pytest.mark.parametrize(
    "filename,mime,kind",
    [
        ("image.png", "image/png", "image"),
        ("image.jpg", "image/jpeg", "image"),
        ("sound.ogg", "audio/ogg", "audio"),
        ("sound.mp3", "audio/mpeg", "audio"),
        ("sound.m4a", "audio/mp4", "audio"),
        ("sound.aac", "audio/aac", "audio"),
        ("video.mp4", "video/mp4", "video"),
        ("document.pdf", "application/pdf", "document"),
        ("document.txt", "text/plain", "document"),
    ],
)
def test_real_supported_media_and_content_hash(samples, filename, mime, kind):
    path = samples / filename
    result = inspect_media(path, mime, filename, normalize=False)
    assert result.kind == kind
    assert result.mime_type == mime
    assert result.size_bytes == path.stat().st_size
    assert result.sha256 == hashlib.sha256(path.read_bytes()).hexdigest()
    assert Path(result.path) == path


def test_image_validation_does_not_depend_on_temporary_extension(samples, tmp_path):
    path = tmp_path / "upload.tmp"
    path.write_bytes((samples / "image.png").read_bytes())
    assert inspect_media(path, "image/png", "original.jpg").filename == "original.png"


def test_browser_recording_converts_to_ogg_opus_and_preserves_input(samples):
    path = samples / "sound.webm"
    original = path.read_bytes()
    converted = inspect_media(
        path, "audio/webm;codecs=opus", "recording.webm", voice_note=True
    )
    try:
        assert converted.mime_type == "audio/ogg"
        assert converted.filename == "recording.ogg"
        assert converted.voice_note
        assert converted.path != str(path)
        assert Path(converted.path).read_bytes().startswith(b"OggS")
        assert path.read_bytes() == original
        assert (
            inspect_media(
                converted.path, "audio/ogg", converted.filename, normalize=False
            ).sha256
            == converted.sha256
        )
    finally:
        Path(converted.path).unlink()


def test_safari_mp4_voice_note_converts_to_provider_format(samples):
    converted = inspect_media(
        samples / "sound.m4a", "audio/mp4", "recording.m4a", voice_note=True
    )
    try:
        assert converted.mime_type == "audio/ogg"
        assert converted.filename.endswith(".ogg")
    finally:
        Path(converted.path).unlink()


def test_webm_without_recording_is_an_opaque_document_not_unsupported_audio(samples):
    result = inspect_media(samples / "sound.webm", "audio/webm", "sound.webm")
    assert result.kind == "document"
    assert result.mime_type == "application/octet-stream"
    assert result.filename == "sound.webm"


def test_playback_rendition_keeps_original_and_is_aac_mp4(samples, tmp_path):
    original = samples / "sound.ogg"
    original_bytes = original.read_bytes()
    playback = create_playback_audio(original, tmp_path / "playback.m4a")
    assert playback.mime_type == "audio/mp4"
    assert playback.kind == "audio"
    assert original.read_bytes() == original_bytes
    assert (
        inspect_media(
            playback.path, "audio/mp4", "playback.m4a", normalize=False
        ).sha256
        == playback.sha256
    )
    with pytest.raises(PolicyError, match="preserve_original"):
        create_playback_audio(original, original)


@pytest.mark.parametrize(
    "mime",
    [
        "image/png",
        "image/jpeg",
        "audio/ogg",
        "audio/mp4",
        "video/mp4",
        "application/pdf",
    ],
)
def test_forged_declared_types_and_magic_are_rejected(tmp_path, mime):
    path = tmp_path / "fake.bin"
    path.write_bytes(b"<html><script>not a media file</script></html>")
    with pytest.raises(PolicyError) as error:
        inspect_media(path, mime, "fake.bin")
    assert error.value.status_code == 415


def test_matching_image_magic_alone_is_not_enough(tmp_path):
    path = tmp_path / "fake.png"
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"not-an-image")
    with pytest.raises(PolicyError):
        inspect_media(path, "image/png", "fake.png")


def test_video_codec_policy_rejects_non_h264(samples):
    with pytest.raises(PolicyError, match="unsupported_video_codec"):
        inspect_media(samples / "bad-video.mp4", "video/mp4", "video.mp4")


def test_audio_mime_rejects_a_video_container(samples):
    with pytest.raises(PolicyError, match="invalid_audio_content"):
        inspect_media(samples / "video.mp4", "audio/mp4", "voice.m4a")


def test_unknown_types_use_opaque_upload_limit_and_known_type_caps_remain(tmp_path):
    for mime in (
        "text/html",
        "image/svg+xml",
        "application/zip",
        "application/octet-stream",
    ):
        assert upload_limit(mime) == MAX_UPLOAD_BYTES
    assert upload_limit("image/jpeg") == LIMITS["image"]
    assert upload_limit("application/pdf") == MAX_UPLOAD_BYTES
    path = tmp_path / "large.png"
    with path.open("wb") as file:
        file.truncate(LIMITS["image"] + 1)
    with pytest.raises(PolicyError) as error:
        inspect_media(path, "image/png", "large.png")
    assert error.value.status_code == 413


@pytest.mark.parametrize("filename", ["sticker.webp", "animated.webp"])
def test_meta_static_and_animated_stickers_are_supported(samples, filename):
    result = inspect_media(samples / filename, "image/webp", filename)
    assert result.kind == "sticker"
    assert result.mime_type == "image/webp"
    assert (
        result.sha256 == hashlib.sha256((samples / filename).read_bytes()).hexdigest()
    )
    validate_caption("sticker", "")
    with pytest.raises(PolicyError, match="sticker_caption"):
        validate_caption("sticker", "not supported by Meta")


def test_sticker_must_use_provider_dimensions(samples, tmp_path):
    path = tmp_path / "small.webp"
    ffmpeg("-i", str(samples / "image.png"), "-c:v", "libwebp", str(path))
    with pytest.raises(PolicyError, match="sticker_dimensions_must_be_512"):
        inspect_media(path, "image/webp", path.name)


@pytest.mark.parametrize("hint", ["", "application/octet-stream", "some/future-format"])
@pytest.mark.parametrize(
    "filename,expected",
    [
        ("image.png", "image/png"),
        ("document.pdf", "application/pdf"),
        ("sound.ogg", "audio/ogg"),
        ("video.mp4", "video/mp4"),
        ("sticker.webp", "image/webp"),
    ],
)
def test_generic_browser_hint_detects_supported_bytes_without_relying_on_name(
    samples, hint, filename, expected
):
    result = inspect_media(samples / filename, hint, "upload.bin")
    assert result.mime_type == expected


def test_unknown_binary_stays_opaque_without_extraction_or_mime_pretence(tmp_path):
    path = tmp_path / "data.zip"
    with zipfile.ZipFile(path, "w") as file:
        file.writestr("notes.txt", "private archive")
    result = inspect_media(path, "application/zip", "data.zip")
    assert result.mime_type == "application/octet-stream"
    assert result.kind == "document"
    assert result.filename == "data.zip"
    assert result.sha256 == hashlib.sha256(path.read_bytes()).hexdigest()
    assert inspect_media(path, "", "data.zip").mime_type == "application/octet-stream"


def test_large_pixel_image_is_not_rejected_by_a_local_pixel_policy(tmp_path):
    path = tmp_path / "phone.png"
    ffmpeg(
        "-f",
        "lavfi",
        "-i",
        "color=c=blue:s=6000x5000",
        "-frames:v",
        "1",
        "-threads",
        "1",
        str(path),
    )
    assert path.stat().st_size < LIMITS["image"]
    assert inspect_media(path, "image/png", path.name).kind == "image"


def test_filename_does_not_have_an_unpublished_180_byte_limit():
    name = "a" * 200 + ".pdf"
    assert safe_filename(name) == name


def test_sticker_static_and_animated_provider_limits(samples, tmp_path):
    source = (samples / "sticker.webp").read_bytes()
    payload = b"metadata" * 13000
    extra = b"EXIF" + len(payload).to_bytes(4, "little") + payload
    data = source + extra
    data = data[:4] + (len(data) - 8).to_bytes(4, "little") + data[8:]
    path = tmp_path / "large.webp"
    path.write_bytes(data)
    assert 100_000 < path.stat().st_size < 500_000
    with pytest.raises(PolicyError) as error:
        inspect_media(path, "image/webp", path.name)
    assert error.value.status_code == 413


def test_ooxml_has_no_unpublished_entry_or_manifest_quota(tmp_path):
    path = tmp_path / "document.docx"
    mime = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    with zipfile.ZipFile(path, "w") as file:
        manifest = (
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            + "<!--"
            + "manifest padding " * 10000
            + "-->"
            + '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml" /></Types>'
        )
        file.writestr("[Content_Types].xml", manifest)
        file.writestr("word/document.xml", "<document/>")
        for number in range(5001):
            file.writestr(f"word/media/part-{number}", b"")
    assert path.stat().st_size < LIMITS["document"]
    assert inspect_media(path, mime, path.name).mime_type == mime
    assert (
        inspect_media(path, "application/octet-stream", "upload.bin").mime_type == mime
    )


@pytest.mark.parametrize(
    "stream_name,mime",
    [
        ("WordDocument", "application/msword"),
        ("Workbook", "application/vnd.ms-excel"),
        ("PowerPoint Document", "application/vnd.ms-powerpoint"),
    ],
)
def test_generic_legacy_office_is_identified_by_compound_streams(
    tmp_path, stream_name, mime
):
    path = tmp_path / "upload.bin"
    path.write_bytes(cfb_document(stream_name))
    assert inspect_media(path, "", "upload.bin").mime_type == mime


@pytest.mark.parametrize(
    "filename",
    [
        "../secret.pdf",
        "foo/bar.pdf",
        "foo\\bar.pdf",
        "a\r\nheader.pdf",
        "a\x00.pdf",
        "..",
        "",
        "a\u202eexe.pdf",
    ],
)
def test_unsafe_filenames_are_rejected(filename):
    with pytest.raises(PolicyError, match="invalid_attachment_filename"):
        safe_filename(filename)


def test_caption_limits_and_audio_no_silent_extra_send():
    validate_caption("image", "a" * 1024)
    validate_caption("audio", "")
    with pytest.raises(PolicyError, match="caption_too_long"):
        validate_caption("video", "a" * 1025)
    with pytest.raises(PolicyError, match="audio_caption"):
        validate_caption("audio", "Do not send this as a separate message")


def test_empty_files_and_plaintext_binary_or_html_are_rejected(tmp_path):
    path = tmp_path / "document.txt"
    for content in (
        b"",
        b"<!DOCTYPE html><p>hello</p>",
        b"\x00binary",
        b"valid prefix" + b"\xff",
    ):
        path.write_bytes(content)
        with pytest.raises(PolicyError):
            inspect_media(path, "text/plain", "document.txt")


def test_ooxml_checks_content_manifest_and_exact_document_kind(tmp_path):
    path = tmp_path / "document.docx"
    document_mime = (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    with zipfile.ZipFile(path, "w") as file:
        file.writestr(
            "[Content_Types].xml",
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml" /></Types>',
        )
        file.writestr("word/document.xml", "<document/>")
    assert inspect_media(path, document_mime, "document.docx").kind == "document"
    with pytest.raises(PolicyError):
        inspect_media(
            path,
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "wrong.xlsx",
        )
    with zipfile.ZipFile(path, "w") as file:
        file.writestr("word/document.xml", "<document/>")
    with pytest.raises(PolicyError):
        inspect_media(path, document_mime, "document.docx")


def cfb_document(stream_name):
    header = bytearray(512)
    header[:8] = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
    struct.pack_into("<H", header, 26, 3)
    header[28:30] = b"\xfe\xff"
    struct.pack_into("<H", header, 30, 9)
    struct.pack_into("<I", header, 44, 1)
    struct.pack_into("<I", header, 48, 1)
    struct.pack_into("<I", header, 68, 0xFFFFFFFE)
    for offset in range(76, 512, 4):
        struct.pack_into("<I", header, offset, 0xFFFFFFFF)
    struct.pack_into("<I", header, 76, 0)
    fat = bytearray(b"\xff" * 512)
    struct.pack_into("<I", fat, 0, 0xFFFFFFFD)
    struct.pack_into("<I", fat, 4, 0xFFFFFFFE)
    directory = bytearray(512)
    name = (stream_name + "\x00").encode("utf-16-le")
    directory[: len(name)] = name
    struct.pack_into("<H", directory, 64, len(name))
    directory[66] = 2
    return bytes(header + fat + directory)


def test_legacy_office_checks_compound_stream_kind(tmp_path):
    path = tmp_path / "document.doc"
    path.write_bytes(cfb_document("WordDocument"))
    assert inspect_media(path, "application/msword", "document.doc").kind == "document"
    with pytest.raises(PolicyError):
        inspect_media(path, "application/vnd.ms-excel", "wrong.xls")
    path.write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"fake")
    with pytest.raises(PolicyError):
        inspect_media(path, "application/msword", "document.doc")


def test_missing_processor_or_timeout_is_reported_not_success(samples, monkeypatch):
    import crm_media_policy

    monkeypatch.setattr(
        crm_media_policy.subprocess,
        "run",
        lambda *a, **k: (_ for _ in ()).throw(subprocess.TimeoutExpired("ffmpeg", 20)),
    )
    with pytest.raises(PolicyError, match="attachment_processing_timeout"):
        inspect_media(samples / "sound.ogg", "audio/ogg", "sound.ogg")
