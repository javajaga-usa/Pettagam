"""Draw the Pettagam icon (a treasure box on a rounded square) as PNGs for an .iconset. Stdlib only.

  python3 make_icon.py OUT_DIR
"""
import os
import struct
import sys
import zlib

BG = (181, 86, 43)       # the panel's accent colour
FG = (255, 255, 255)


def png(size, pixels):
    raw = b"".join(b"\x00" + bytes(c for px in pixels[y * size:(y + 1) * size] for c in px) for y in range(size))

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def draw(size):
    s = size / 32.0
    pad, r = 2 * s, 7 * s
    px = []
    for y in range(size):
        for x in range(size):
            cx, cy = x + 0.5, y + 0.5
            # rounded square
            dx = max(pad + r - cx, 0, cx - (size - pad - r))
            dy = max(pad + r - cy, 0, cy - (size - pad - r))
            inside = cx >= pad and cx <= size - pad and cy >= pad and cy <= size - pad and dx * dx + dy * dy <= r * r
            if not inside:
                px.append((0, 0, 0, 0))
                continue
            u, v = cx / s, cy / s   # 32x32 design grid
            w = 1.6
            box = 8 <= u <= 24 and 13 <= v <= 25
            lid = 7 <= u <= 25 and 9 <= v <= 13
            edge = (box and (u < 8 + w or u > 24 - w or v > 25 - w)) or (lid and (u < 7 + w or u > 25 - w or v < 9 + w or v > 13 - w))
            clasp = 14.5 <= u <= 17.5 and 12 <= v <= 17
            px.append((*FG, 255) if edge or clasp else (*BG, 255))
    return png(size, px)


def main(out):
    os.makedirs(out, exist_ok=True)
    for size in (16, 32, 64, 128, 256, 512):
        with open(os.path.join(out, f"icon_{size}x{size}.png"), "wb") as f:
            f.write(draw(size))
        with open(os.path.join(out, f"icon_{size}x{size}@2x.png"), "wb") as f:
            f.write(draw(size * 2))


if __name__ == "__main__":
    main(sys.argv[1])
