#!/usr/bin/env python3
"""Generate dependency-free PNG assets with the required Kommo dimensions."""

import binascii
import struct
import zlib
from pathlib import Path

SIZES = {
    "logo.png": (130, 100),
    "logo_main.png": (400, 272),
    "logo_small.png": (108, 108),
    "logo_min.png": (84, 84),
    "logo_medium.png": (240, 84),
}


def chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", binascii.crc32(kind + data) & 0xFFFFFFFF)


def png(width: int, height: int) -> bytes:
    rows = []
    for y in range(height):
        row = bytearray([0])
        for x in range(width):
            # Purple/blue gradient with a white central spark, recognizable at
            # every required size without depending on fonts or image packages.
            r, g, b = 74 + (x * 42 // width), 59 + (y * 38 // height), 190 + (x * 35 // width)
            cx, cy = width // 2, height // 2
            if abs(x - cx) <= max(1, width // 28) or abs(y - cy) <= max(1, height // 28):
                if abs(x - cx) + abs(y - cy) < min(width, height) // 3:
                    r, g, b = 255, 255, 255
            row.extend((r, g, b, 255))
        rows.append(bytes(row))
    header = b"\x89PNG\r\n\x1a\n"
    return header + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(b"".join(rows), 9)) + chunk(b"IEND", b"")


output = Path(__file__).parent / "images"
output.mkdir(exist_ok=True)
for filename, dimensions in SIZES.items():
    (output / filename).write_bytes(png(*dimensions))
