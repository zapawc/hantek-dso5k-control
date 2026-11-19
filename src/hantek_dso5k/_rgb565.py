"""Write a provisional PNG from the raw graphics buffer, one scan line at a time."""
import struct
import zlib


def chunk(handle, name, data):
    handle.write(struct.pack(">I", len(data)))
    handle.write(name)
    handle.write(data)
    handle.write(struct.pack(">I", zlib.crc32(data, zlib.crc32(name))))


def rgb565(line, order):
    expanded = bytearray(1 + len(line) * 3 // 2)
    for idx, (pixel,) in enumerate(struct.iter_unpack("<H" if order == "little" else ">H", line)):
        red, green, blue = pixel >> 11, (pixel >> 5) & 63, pixel & 31
        pos = 1 + idx * 3
        expanded[pos] = (red << 3) | (red >> 2)
        expanded[pos + 1] = (green << 2) | (green >> 4)
        expanded[pos + 2] = (blue << 3) | (blue >> 2)
    return expanded


def render(source, target, *, width=800, height=480, stride=1600, order="little"):
    if width <= 0 or height <= 0 or stride < width * 2 or order not in ("little", "big"):
        raise ValueError("Invalid image layout")
    if source.stat().st_size < height * stride:
        raise ValueError("Buffer too short for requested layout")
    with source.open("rb") as raw, target.open("xb") as png:
        png.write(b"\x89PNG\r\n\x1a\n")
        chunk(png, b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        compress = zlib.compressobj()
        for _ in range(height):
            row = raw.read(stride)
            data = compress.compress(rgb565(row[:width * 2], order))
            if data:
                chunk(png, b"IDAT", data)
        chunk(png, b"IDAT", compress.flush())
        chunk(png, b"IEND", b"")
