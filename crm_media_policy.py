"""Bounded local media validation/conversion for the CRM; no Chatwoot dependency.

Temporary paths returned by this module are processing buffers, not archives.
The caller owns and removes a converted path when it differs from its input.
"""

from __future__ import annotations

import codecs
import hashlib
import os
import re
import struct
import subprocess
import tempfile
import time
import unicodedata
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO
from xml.etree import ElementTree

import imageio_ffmpeg

MAX_UPLOAD_BYTES = 100_000_000
LIMITS = {
    "image": 5_000_000,
    "audio": 16_000_000,
    "video": 16_000_000,
    "document": 100_000_000,
    "sticker": 500_000,
}
MIMES = {
    "application/octet-stream": ("document", ""),
    "image/jpeg": ("image", ".jpg"),
    "image/png": ("image", ".png"),
    "image/webp": ("sticker", ".webp"),
    "audio/aac": ("audio", ".aac"),
    "audio/amr": ("audio", ".amr"),
    "audio/mpeg": ("audio", ".mp3"),
    "audio/mp4": ("audio", ".m4a"),
    "audio/ogg": ("audio", ".ogg"),
    "audio/webm": ("audio", ".webm"),
    "video/mp4": ("video", ".mp4"),
    "video/3gpp": ("video", ".3gp"),
    "application/pdf": ("document", ".pdf"),
    "text/plain": ("document", ".txt"),
    "application/msword": ("document", ".doc"),
    "application/vnd.ms-excel": ("document", ".xls"),
    "application/vnd.ms-powerpoint": ("document", ".ppt"),
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": (
        "document",
        ".docx",
    ),
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": (
        "document",
        ".xlsx",
    ),
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": (
        "document",
        ".pptx",
    ),
}
SUPPORTED_MIMES = tuple(MIMES)


class PolicyError(ValueError):
    def __init__(self, code: str, status_code: int = 422):
        self.code, self.status_code = code, status_code
        super().__init__(code)


MediaError = PolicyError


@dataclass(frozen=True)
class PreparedMedia:
    path: str
    mime_type: str
    kind: str
    filename: str
    size_bytes: int
    sha256: str
    voice_note: bool = False


def normalized_mime(value: str) -> str:
    mime = str(value or "").split(";", 1)[0].strip().lower()
    return {
        "audio/mp3": "audio/mpeg",
        "audio/x-m4a": "audio/mp4",
        "image/jpg": "image/jpeg",
    }.get(mime, mime)


def safe_filename(value: str) -> str:
    name = unicodedata.normalize("NFC", str(value or "")).strip()
    if (
        not name
        or name in {".", ".."}
        or any(unicodedata.category(c) in {"Cc", "Cf"} or c in "/\\" for c in name)
    ):
        raise PolicyError("invalid_attachment_filename")
    return re.sub(r'["<>|:*?]', "_", name)


def upload_limit(mime: str) -> int:
    mime = normalized_mime(mime)
    if mime not in MIMES:
        # Unknown files stay opaque documents. Meta's upload endpoint remains
        # authoritative; no upload is described as an accepted message.
        return MAX_UPLOAD_BYTES
    return LIMITS[MIMES[mime][0]]


def validate_caption(kind: str, text: str | None) -> None:
    text = text or ""
    if kind not in LIMITS:
        raise PolicyError("unsupported_attachment_type", 415)
    if kind in {"audio", "sticker"} and text.strip():
        raise PolicyError(f"{kind}_caption_not_supported", 422)
    if len(text) > 1024:
        raise PolicyError("attachment_caption_too_long", 422)


def file_digest(stream: BinaryIO) -> tuple[int, str]:
    stream.seek(0)
    digest, total = hashlib.sha256(), 0
    while chunk := stream.read(64 * 1024):
        total += len(chunk)
        if total > MAX_UPLOAD_BYTES:
            raise PolicyError("attachment_too_large", 413)
        digest.update(chunk)
    stream.seek(0)
    return total, digest.hexdigest()


def _demuxer(mime: str) -> str:
    return {
        "image/jpeg": "image2",
        "image/png": "image2",
        "image/webp": "webp_pipe",
        "audio/mpeg": "mp3",
        "audio/aac": "aac",
        "audio/amr": "amr",
        "audio/ogg": "ogg",
        "audio/webm": "matroska",
        "audio/mp4": "mov",
        "video/mp4": "mov",
        "video/3gpp": "mov",
    }[mime]


def _run_ffmpeg(arguments: list[str], *, timeout: int = 20) -> str:
    try:
        executable = imageio_ffmpeg.get_ffmpeg_exe()
        with tempfile.TemporaryFile(mode="w+b") as diagnostic:
            result = subprocess.run(
                [
                    executable,
                    "-nostdin",
                    "-hide_banner",
                    "-nostats",
                    "-threads",
                    "1",
                    *arguments,
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=diagnostic,
                timeout=timeout,
                check=False,
            )
            if diagnostic.tell() > 256 * 1024:
                raise PolicyError("invalid_attachment_content", 415)
            diagnostic.seek(0)
            text = diagnostic.read(256 * 1024).decode("utf-8", errors="replace")
    except subprocess.TimeoutExpired as error:
        raise PolicyError("attachment_processing_timeout", 422) from error
    except (OSError, RuntimeError) as error:
        raise PolicyError("attachment_processor_unavailable", 503) from error
    if result.returncode:
        raise PolicyError("invalid_attachment_content", 415)
    return text


def _input(path: str, mime: str) -> list[str]:
    # A fixed demuxer and local-only protocols forbid playlists/network sources.
    decoder = (
        [
            "-c:v",
            {"image/png": "png", "image/jpeg": "mjpeg", "image/webp": "webp"}[mime],
        ]
        if mime.startswith("image/")
        else []
    )
    return [
        "-protocol_whitelist",
        "file,pipe",
        "-f",
        _demuxer(mime),
        *decoder,
        "-i",
        str(path),
    ]


def _probe(path: str, mime: str) -> str:
    text = _run_ffmpeg([*_input(path, mime), "-map", "0", "-t", "0", "-f", "null", "-"])
    if "Stream #" not in text:
        raise PolicyError("invalid_attachment_content", 415)
    if mime.startswith("video/"):
        if not re.search(r"Video:\s*h264\b", text):
            raise PolicyError("unsupported_video_codec", 415)
        if "Audio:" in text and not re.search(r"Audio:\s*aac\b", text):
            raise PolicyError("unsupported_video_audio_codec", 415)
    if mime.startswith("audio/"):
        if "Audio:" not in text or "Video:" in text:
            raise PolicyError("invalid_audio_content", 415)
        required = {
            "audio/ogg": "opus",
            "audio/webm": "opus",
            "audio/mp4": "aac",
            "audio/aac": "aac",
            "audio/amr": r"amr(?:_nb|_wb)?",
            "audio/mpeg": "mp3",
        }[mime]
        if not re.search(rf"Audio:\s*{required}\b", text):
            raise PolicyError("unsupported_audio_codec", 415)
    if mime.startswith("image/"):
        _run_ffmpeg(
            [
                "-xerror",
                "-abort_on",
                "empty_output",
                *_input(path, mime),
                "-map",
                "0:v:0",
                "-frames:v",
                "1",
                "-threads",
                "1",
                "-f",
                "null",
                "-",
            ]
        )
    elif mime.startswith("audio/"):
        _run_ffmpeg(
            [
                "-xerror",
                "-abort_on",
                "empty_output",
                *_input(path, mime),
                "-map",
                "0:a:0",
                "-frames:a",
                "1",
                "-threads",
                "1",
                "-f",
                "null",
                "-",
            ]
        )
    elif mime.startswith("video/"):
        _run_ffmpeg(
            [
                "-xerror",
                "-abort_on",
                "empty_output",
                *_input(path, mime),
                "-map",
                "0:v:0",
                "-frames:v",
                "1",
                "-threads",
                "1",
                "-f",
                "null",
                "-",
            ]
        )
        if "Audio:" in text:
            _run_ffmpeg(
                [
                    "-xerror",
                    "-abort_on",
                    "empty_output",
                    *_input(path, mime),
                    "-map",
                    "0:a:0",
                    "-frames:a",
                    "1",
                    "-threads",
                    "1",
                    "-f",
                    "null",
                    "-",
                ]
            )
    return text


def _valid_cfb(path: str, mime: str) -> bool:
    """Inspect bounded compound-document directories, not only a shared magic."""
    expected = {
        "application/msword": {"worddocument"},
        "application/vnd.ms-excel": {"workbook", "book"},
        "application/vnd.ms-powerpoint": {"powerpoint document"},
    }[mime]
    try:
        with open(path, "rb") as document:
            header = document.read(512)
            if (
                len(header) != 512
                or header[:8] != b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
                or header[28:30] != b"\xfe\xff"
            ):
                return False
            shift = struct.unpack_from("<H", header, 30)[0]
            if shift not in {9, 12}:
                return False
            sector_size = 1 << shift
            sector_count = os.fstat(document.fileno()).st_size // sector_size - 1

            def sector(number: int) -> bytes:
                if not 0 <= number < sector_count:
                    raise ValueError("invalid CFB sector")
                document.seek((number + 1) * sector_size)
                return document.read(sector_size)

            fat_count = struct.unpack_from("<I", header, 44)[0]
            if not 1 <= fat_count <= sector_count:
                return False
            fat_ids = [
                number
                for number in struct.unpack_from("<109I", header, 76)
                if number < 0xFFFFFFFA
            ]
            difat = struct.unpack_from("<I", header, 68)[0]
            seen = set()
            while len(fat_ids) < fat_count:
                if difat in seen or len(seen) >= sector_count:
                    return False
                seen.add(difat)
                values = struct.unpack(f"<{sector_size // 4}I", sector(difat))
                fat_ids.extend(number for number in values[:-1] if number < 0xFFFFFFFA)
                difat = values[-1]
            fat = []
            for number in fat_ids[:fat_count]:
                fat.extend(struct.unpack(f"<{sector_size // 4}I", sector(number)))
            directory = struct.unpack_from("<I", header, 48)[0]
            seen = set()
            found = set()
            while directory != 0xFFFFFFFE:
                if (
                    directory in seen
                    or len(seen) >= sector_count
                    or directory >= len(fat)
                ):
                    return False
                seen.add(directory)
                data = sector(directory)
                for offset in range(0, sector_size, 128):
                    length = struct.unpack_from("<H", data, offset + 64)[0]
                    if data[offset + 66] == 2 and 2 <= length <= 64 and length % 2 == 0:
                        found.add(
                            data[offset : offset + length - 2]
                            .decode("utf-16-le")
                            .lower()
                        )
                directory = fat[directory]
            return bool(expected.intersection(found))
    except (OSError, UnicodeError, ValueError, struct.error):
        return False


def _valid_document(path: str, mime: str, prefix: bytes) -> bool:
    if mime == "application/pdf":
        with open(path, "rb") as source:
            source.seek(max(0, os.path.getsize(path) - 4096))
            tail = source.read(4096)
        return bool(re.match(rb"%PDF-[12]\.\d", prefix) and b"%%EOF" in tail)
    if mime == "text/plain":
        if (
            prefix.lstrip(b"\xef\xbb\xbf \t\r\n")
            .lower()
            .startswith((b"<html", b"<!doctype", b"<svg", b"<?xml"))
        ):
            return False
        decoder = codecs.getincrementaldecoder("utf-8")()
        try:
            with open(path, "rb") as source:
                while chunk := source.read(64 * 1024):
                    if b"\x00" in chunk:
                        return False
                    decoder.decode(chunk)
                decoder.decode(b"", final=True)
            return True
        except UnicodeError:
            return False
    if mime in {
        "application/msword",
        "application/vnd.ms-excel",
        "application/vnd.ms-powerpoint",
    }:
        return _valid_cfb(path, mime)
    part = (
        "word/document.xml"
        if "wordprocessing" in mime
        else "xl/workbook.xml"
        if "spreadsheet" in mime
        else "ppt/presentation.xml"
    )
    try:
        with zipfile.ZipFile(path) as document:
            entries = document.infolist()
            names = {entry.filename for entry in entries}
            if (
                any(entry.flag_bits & 1 for entry in entries)
                or any(
                    name.startswith("/") or ".." in name.split("/") or "\\" in name
                    for name in names
                )
                or "[Content_Types].xml" not in names
                or part not in names
            ):
                return False
            # OOXML main-part types differ from the external package MIME.
            expected = {
                "word": "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml",
                "xl": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml",
                "ppt": "application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml",
            }[part.split("/")[0]]
            # Do not reject otherwise valid documents for unpublished entry,
            # expanded-archive or manifest-size quotas. Only inspect the part
            # needed to establish its real package MIME, without extraction.
            deadline = time.monotonic() + 20
            with document.open("[Content_Types].xml") as manifest:
                for event, element in ElementTree.iterparse(
                    manifest, events=("start", "end")
                ):
                    if time.monotonic() > deadline:
                        raise PolicyError("attachment_processing_timeout", 422)
                    if (
                        event == "start"
                        and element.attrib.get("ContentType") == expected
                    ):
                        return True
                    if event == "end":
                        element.clear()
            return False
    except (OSError, ValueError, zipfile.BadZipFile, ElementTree.ParseError, KeyError):
        return False


def _convert_audio(path: str, mime: str, target: str, *, playback: bool) -> None:
    output = (
        ["-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", "-f", "mp4"]
        if playback
        else ["-c:a", "libopus", "-b:a", "32k", "-f", "ogg"]
    )
    _run_ffmpeg(
        [
            "-y",
            "-loglevel",
            "error",
            *_input(path, mime),
            "-vn",
            "-map_metadata",
            "-1",
            "-ac",
            "1",
            "-ar",
            "48000",
            "-threads",
            "1",
            *output,
            "-fs",
            str(LIMITS["audio"] + 1),
            str(target),
        ],
        timeout=30,
    )
    if os.path.getsize(target) > LIMITS["audio"]:
        raise PolicyError("attachment_too_large", 413)


def _infer_mime(path: str, prefix: bytes, *, voice_note: bool) -> str:
    """Resolve a generic browser hint from bytes, never from its extension alone."""
    if prefix.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if prefix.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if prefix[:4] == b"RIFF" and prefix[8:12] == b"WEBP":
        return "image/webp"
    if prefix.startswith(b"OggS"):
        return "audio/ogg"
    if prefix.startswith((b"#!AMR\n", b"#!AMR-WB\n")):
        return "audio/amr"
    if len(prefix) > 1 and prefix[0] == 0xFF and prefix[1] & 0xF6 == 0xF0:
        return "audio/aac"
    if prefix.startswith(b"ID3") or (
        len(prefix) > 1 and prefix[0] == 0xFF and prefix[1] & 0xE0 == 0xE0
    ):
        return "audio/mpeg"
    if prefix.startswith(b"\x1aE\xdf\xa3"):
        return "audio/webm" if voice_note else "application/octet-stream"
    if len(prefix) >= 12 and prefix[4:8] == b"ftyp":
        probe = _run_ffmpeg(
            [*_input(path, "video/mp4"), "-map", "0", "-t", "0", "-f", "null", "-"]
        )
        return (
            ("video/3gpp" if prefix[8:12].startswith(b"3gp") else "video/mp4")
            if "Video:" in probe
            else "audio/mp4"
        )
    for mime in (
        "application/pdf",
        "application/msword",
        "application/vnd.ms-excel",
        "application/vnd.ms-powerpoint",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "text/plain",
    ):
        if _valid_document(path, mime, prefix):
            return mime
    return "application/octet-stream"


def _webp_chunks(data: bytes, start: int, end: int) -> list[tuple[bytes, bytes]]:
    chunks = []
    while start < end:
        if start + 8 > end:
            raise PolicyError("invalid_sticker_content", 415)
        length = int.from_bytes(data[start + 4 : start + 8], "little")
        payload_end = start + 8 + length
        next_chunk = payload_end + (length & 1)
        if next_chunk > end:
            raise PolicyError("invalid_sticker_content", 415)
        chunks.append((data[start : start + 4], data[start + 8 : payload_end]))
        start = next_chunk
    return chunks


def _webp_dimensions(kind: bytes, payload: bytes) -> tuple[int, int] | None:
    if kind == b"VP8X" and len(payload) >= 10:
        return int.from_bytes(payload[4:7], "little") + 1, int.from_bytes(
            payload[7:10], "little"
        ) + 1
    if kind == b"VP8 " and len(payload) >= 10 and payload[3:6] == b"\x9d\x01\x2a":
        return int.from_bytes(payload[6:8], "little") & 0x3FFF, int.from_bytes(
            payload[8:10], "little"
        ) & 0x3FFF
    if kind == b"VP8L" and len(payload) >= 5 and payload[0] == 0x2F:
        values = int.from_bytes(payload[1:5], "little")
        return (values & 0x3FFF) + 1, ((values >> 14) & 0x3FFF) + 1
    return None


def _validate_webp(path: str, size: int) -> None:
    with open(path, "rb") as source:
        data = source.read(LIMITS["sticker"] + 1)
    if data[:4] != b"RIFF" or data[8:12] != b"WEBP" or len(data) < 20:
        raise PolicyError("invalid_sticker_content", 415)
    if int.from_bytes(data[4:8], "little") + 8 != len(data):
        raise PolicyError("invalid_sticker_content", 415)
    chunks = _webp_chunks(data, 12, len(data))
    extended = next((payload for kind, payload in chunks if kind == b"VP8X"), None)
    animated = bool(extended and extended[0] & 0x02)
    dimensions = (
        _webp_dimensions(b"VP8X", extended)
        if extended
        else next(
            (
                dimensions
                for kind, payload in chunks
                if (dimensions := _webp_dimensions(kind, payload))
            ),
            None,
        )
    )
    if dimensions != (512, 512):
        raise PolicyError("sticker_dimensions_must_be_512", 415)
    if size > (500_000 if animated else 100_000):
        raise PolicyError("attachment_too_large", 413)
    if animated:
        frames = [payload for kind, payload in chunks if kind == b"ANMF"]
        if not frames or not any(kind == b"ANIM" for kind, _ in chunks):
            raise PolicyError("invalid_sticker_content", 415)
        for frame in frames:
            if len(frame) < 24:
                raise PolicyError("invalid_sticker_content", 415)
            frame_width, frame_height = (
                int.from_bytes(frame[6:9], "little") + 1,
                int.from_bytes(frame[9:12], "little") + 1,
            )
            x, y = (
                int.from_bytes(frame[:3], "little") * 2,
                int.from_bytes(frame[3:6], "little") * 2,
            )
            parts = _webp_chunks(frame, 16, len(frame))
            encoded = [part for part in parts if part[0] in {b"VP8 ", b"VP8L"}]
            if (
                len(encoded) != 1
                or _webp_dimensions(*encoded[0]) != (frame_width, frame_height)
                or x + frame_width > 512
                or y + frame_height > 512
            ):
                raise PolicyError("invalid_sticker_content", 415)
    else:
        if not any(kind in {b"VP8 ", b"VP8L"} for kind, _ in chunks):
            raise PolicyError("invalid_sticker_content", 415)
        _probe(path, "image/webp")


def inspect_media(
    path: str | os.PathLike[str],
    declared_mime: str,
    filename: str,
    *,
    voice_note: bool = False,
    normalize: bool = True,
) -> PreparedMedia:
    """Validate real content/codecs; normalize only a local outgoing audio copy."""
    path = os.fspath(path)
    mime, filename = normalized_mime(declared_mime), safe_filename(filename)
    maximum = upload_limit(mime)
    with open(path, "rb") as source:
        size, digest = file_digest(source)
        prefix = source.read(65536)
    if not size:
        raise PolicyError("empty_attachment")
    if size > maximum:
        raise PolicyError("attachment_too_large", 413)
    if mime not in MIMES or mime == "application/octet-stream":
        mime = _infer_mime(path, prefix, voice_note=voice_note)
        if size > upload_limit(mime):
            raise PolicyError("attachment_too_large", 413)
    if mime == "audio/webm" and not voice_note:
        mime = "application/octet-stream"
    kind, extension = MIMES[mime]
    if voice_note and kind != "audio":
        raise PolicyError("voice_note_requires_audio", 422)
    valid = False
    if mime == "application/octet-stream":
        # Not rendered inline and never parsed/extracted. Uploading this private
        # opaque object does not assert Meta supports sending its contents.
        valid = True
    elif kind == "document":
        valid = _valid_document(path, mime, prefix)
    elif kind == "sticker":
        _validate_webp(path, size)
        valid = True
    else:
        if mime == "image/jpeg":
            valid = prefix.startswith(b"\xff\xd8\xff")
        elif mime == "image/png":
            valid = prefix.startswith(b"\x89PNG\r\n\x1a\n")
        elif mime == "audio/ogg":
            valid = prefix.startswith(b"OggS")
        elif mime == "audio/amr":
            valid = prefix.startswith((b"#!AMR\n", b"#!AMR-WB\n"))
        elif mime == "audio/aac":
            valid = len(prefix) > 1 and prefix[0] == 0xFF and prefix[1] & 0xF6 == 0xF0
        elif mime == "audio/mpeg":
            valid = prefix.startswith(b"ID3") or (
                len(prefix) > 1 and prefix[0] == 0xFF and prefix[1] & 0xE0 == 0xE0
            )
        elif mime == "audio/webm":
            valid = voice_note and prefix.startswith(b"\x1aE\xdf\xa3")
        elif mime in {"audio/mp4", "video/mp4", "video/3gpp"}:
            brand = prefix[8:12]
            valid = (
                len(prefix) > 12
                and prefix[4:8] == b"ftyp"
                and (brand.startswith(b"3gp") if mime == "video/3gpp" else True)
            )
        if valid:
            probe = _probe(path, mime)
            if (
                normalize
                and voice_note
                and kind == "audio"
                and (mime != "audio/ogg" or "mono" not in probe)
            ):
                descriptor, target = tempfile.mkstemp(
                    prefix="crm-voice-", suffix=".ogg"
                )
                os.close(descriptor)
                try:
                    _convert_audio(path, mime, target, playback=False)
                    return inspect_media(
                        target,
                        "audio/ogg",
                        Path(filename).stem + ".ogg",
                        voice_note=True,
                        normalize=False,
                    )
                except Exception:
                    Path(target).unlink(missing_ok=True)
                    raise
    if not valid:
        raise PolicyError("attachment_type_content_mismatch", 415)
    filename = safe_filename(Path(filename).stem + extension) if extension else filename
    return PreparedMedia(path, mime, kind, filename, size, digest, voice_note)


def create_playback_audio(
    source_path: str | os.PathLike[str],
    target_path: str | os.PathLike[str],
    mime_type: str = "audio/ogg",
) -> PreparedMedia:
    """Create AAC/MP4 playback while preserving the original byte-for-byte."""
    source_path, target_path = os.fspath(source_path), os.fspath(target_path)
    if Path(source_path).resolve() == Path(target_path).resolve():
        raise PolicyError("playback_must_preserve_original")
    mime_type = normalized_mime(mime_type)
    upload_limit(mime_type)
    source = inspect_media(
        source_path, mime_type, "audio" + MIMES[mime_type][1], normalize=False
    )
    if source.kind != "audio":
        raise PolicyError("playback_only_supports_audio", 415)
    try:
        _convert_audio(source.path, source.mime_type, target_path, playback=True)
        return inspect_media(target_path, "audio/mp4", "audio.m4a", normalize=False)
    except Exception:
        Path(target_path).unlink(missing_ok=True)
        raise
