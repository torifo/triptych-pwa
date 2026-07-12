#!/usr/bin/env python3
"""
triptych の PWA アイコンを生成する。
外部依存なし（zlib/struct のみ）で PNG を直書きする。

意匠: mono 世界観（黒地に白の三連画=triptych モチーフ）。
       3本の縦バーを中央に配置し、maskable アイコンとして
       safe zone（中心80%）内に収める。
"""
import struct
import zlib
from pathlib import Path

OUT_DIR = Path(__file__).resolve().parent.parent / "public"

BG = (10, 10, 10)  # #0a0a0a
FG = (242, 242, 242)  # #f2f2f2


def make_png(size: int, bg=BG, fg=FG, padding_ratio: float = 0.18) -> bytes:
    """size x size の正方形 PNG バイト列を生成する。

    中央に triptych（3本の縦バー）を描画する。
    padding_ratio はキャンバス端からのマージン比率（maskable safe zone を意識）。
    """
    pad = int(size * padding_ratio)
    content = size - 2 * pad
    gap = max(1, content // 8)
    bar_w = (content - 2 * gap) // 3
    top = pad
    bottom = size - pad
    bar_x = [pad + i * (bar_w + gap) for i in range(3)]

    rows = []
    for y in range(size):
        row = bytearray()
        in_v_range = top <= y < bottom
        for x in range(size):
            is_bar = False
            if in_v_range:
                for bx in bar_x:
                    if bx <= x < bx + bar_w:
                        is_bar = True
                        break
            color = fg if is_bar else bg
            row.extend(color)
        rows.append(bytes(row))

    raw = b"".join(b"\x00" + row for row in rows)
    compressed = zlib.compress(raw, 9)

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)  # RGB, 8bit
    png = sig + chunk(b"IHDR", ihdr) + chunk(b"IDAT", compressed) + chunk(b"IEND", b"")
    return png


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    targets = [
        ("icon-192.png", 192, 0.18),
        ("icon-512.png", 512, 0.18),
        # apple-touch-icon は角丸をOSが自動適用するため padding をやや広めに
        ("apple-touch-icon.png", 180, 0.20),
    ]
    for name, size, pad in targets:
        data = make_png(size, padding_ratio=pad)
        path = OUT_DIR / name
        path.write_bytes(data)
        print(f"wrote {path} ({len(data)} bytes)")


if __name__ == "__main__":
    main()
