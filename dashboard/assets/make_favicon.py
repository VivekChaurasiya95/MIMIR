"""Render dashboard/assets/hebitus.svg into a transparent PNG favicon.

Stdlib only (no pip install): mirrors the SVG geometry exactly, rasterizes
with 4x4 supersampling for smooth edges, and writes a 256x256 RGBA PNG that
browsers downscale crisply to 16/32/48px tab icons.

Run once from anywhere:
    python dashboard/assets/make_favicon.py
"""

from __future__ import annotations

import binascii
import struct
import zlib
from pathlib import Path

SIZE = 256          # output pixels (square)
GRID = 64.0         # SVG viewBox units
SUPER = 4           # supersampling per axis

TILE = (2.0, 2.0, 60.0, 60.0, 15.0)  # x, y, w, h, rx (SVG units)
STROKES = (  # (x1, y1, x2, y2, half_width)
    (23.0, 20.0, 23.0, 44.0, 4.0),
    (41.0, 20.0, 41.0, 44.0, 4.0),
    (23.0, 32.0, 41.0, 32.0, 4.0),
)
DOT = (48.5, 14.0, 4.0)  # cx, cy, r

INDIGO = (42, 20, 180)
WHITE = (255, 255, 255)
MINT = (153, 239, 229)


def _in_round_rect(px: float, py: float) -> bool:
    x, y, w, h, rx = TILE
    if not (x <= px <= x + w and y <= py <= y + h):
        return False
    cx = min(max(px, x + rx), x + w - rx)
    cy = min(max(py, y + rx), y + h - rx)
    return (px - cx) ** 2 + (py - cy) ** 2 <= rx * rx


def _dist_to_seg(px: float, py: float, x1: float, y1: float,
                 x2: float, y2: float) -> float:
    dx, dy = x2 - x1, y2 - y1
    if dx == 0 and dy == 0:
        return ((px - x1) ** 2 + (py - y1) ** 2) ** 0.5
    t = min(1.0, max(0.0, ((px - x1) * dx + (py - y1) * dy) / (dx * dx + dy * dy)))
    return ((px - (x1 + t * dx)) ** 2 + (py - (y1 + t * dy)) ** 2) ** 0.5


def _classify(px: float, py: float) -> tuple[int, int, int] | None:
    """Topmost logo color at a sample point, or None if transparent."""
    dx, dy, dr = DOT[0] - px, DOT[1] - py, DOT[2]
    if dx * dx + dy * dy <= dr * dr:
        return MINT
    for x1, y1, x2, y2, hw in STROKES:
        if _dist_to_seg(px, py, x1, y1, x2, y2) <= hw:
            return WHITE
    if _in_round_rect(px, py):
        return INDIGO
    return None


def _render() -> list[list[int]]:
    unit = GRID / SIZE  # SVG units per pixel
    rows: list[list[int]] = []
    for row in range(SIZE):
        out: list[int] = []
        for col in range(SIZE):
            cov = [0, 0, 0, 0]  # r, g, b, alpha accumulators
            for sy in range(SUPER):
                py = (row + (sy + 0.5) / SUPER) * unit
                for sx in range(SUPER):
                    px = (col + (sx + 0.5) / SUPER) * unit
                    hit = _classify(px, py)
                    if hit is not None:
                        cov[0] += hit[0]
                        cov[1] += hit[1]
                        cov[2] += hit[2]
                        cov[3] += 255
            n = SUPER * SUPER
            a = round(cov[3] / n)
            if a == 0:
                out.extend((0, 0, 0, 0))
            else:
                out.extend((round(cov[0] / n), round(cov[1] / n), round(cov[2] / n), a))
        rows.append(out)
    return rows


def _write_png(path: Path, rows: list[list[int]]) -> None:
    def chunk(typ: bytes, data: bytes) -> bytes:
        head = struct.pack(">I", len(data)) + typ + data
        return head + struct.pack(">I", binascii.crc32(typ + data) & 0xFFFFFFFF)

    raw = b"".join(b"\x00" + bytes(r) for r in rows)
    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", SIZE, SIZE, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )
    path.write_bytes(png)


def main() -> None:
    dest = Path(__file__).resolve().parent / "hebitus-256.png"
    _write_png(dest, _render())
    print(f"wrote {dest} ({dest.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
