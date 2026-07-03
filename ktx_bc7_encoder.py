#!/usr/bin/env python3
"""
BC7 (mode 6) энкодер + упаковка в KTX v1
==========================================
Позволяет закодировать произвольную PNG-картинку обратно в .ktx формат,
который использует Sky: Children of the Light. Полезно для модов текстур,
если игра не проверяет контрольные суммы ресурсов.

Кодирует ВСЕГДА mode 6 (single-subset, endpoints 7bit+Pbit = честные 8 бит
без потерь, только обычная блочная интерполяция 4x4 как в любом BCn-формате).
Даёт не самое оптимальное сжатие по сравнению с "умным" энкодером, что
перебирает все 8 модов + партиции, но зато простой, корректный и рабочий.

Использование:
    python3 ktx_bc7_encoder.py input.png original.ktx output.ktx

    input.png    - новая текстура (любых размеров, но лучше делать
                   такого же размера, как оригинал, чтобы не путать игру)
    original.ktx - оригинальный .ktx игры (берём оттуда заголовок/формат)
    output.ktx   - куда сохранить результат
"""
import struct
import sys
from PIL import Image

WEIGHTS4 = [0, 4, 9, 13, 17, 21, 26, 30, 34, 38, 43, 47, 51, 55, 60, 64]


def interpolate(e0, e1, weight):
    return tuple(((64 - weight) * a + weight * b + 32) >> 6 for a, b in zip(e0, e1))


def encode_endpoint(vals4):
    """Подбирает 7-битное значение + общий P-bit для 4 компонент (R,G,B,A),
    минимизируя суммарную ошибку округления."""
    best = None
    for pbit in (0, 1):
        raws = []
        err = 0
        for v in vals4:
            raw = (v - pbit + 1) // 2
            raw = max(0, min(127, raw))
            recon = raw * 2 + pbit
            err += abs(recon - v)
            raws.append(raw)
        if best is None or err < best[0]:
            best = (err, pbit, raws)
    return best[1], best[2]  # pbit, [raw_r, raw_g, raw_b, raw_a]


def encode_block_mode6(block_pixels):
    """block_pixels: список из 16 кортежей (R,G,B,A), 0-255, в raster-порядке."""
    e0f = tuple(min(p[c] for p in block_pixels) for c in range(4))
    e1f = tuple(max(p[c] for p in block_pixels) for c in range(4))

    pbit0, raw0 = encode_endpoint(e0f)
    pbit1, raw1 = encode_endpoint(e1f)
    e0 = tuple(r * 2 + pbit0 for r in raw0)
    e1 = tuple(r * 2 + pbit1 for r in raw1)

    indices = []
    for i, p in enumerate(block_pixels):
        best_idx, best_err = 0, None
        rng = range(8) if i == 0 else range(16)  # anchor-пиксель: старший бит=0
        for idx in rng:
            w = WEIGHTS4[idx]
            interp = interpolate(e0, e1, w)
            err = sum((a - b) ** 2 for a, b in zip(interp, p))
            if best_err is None or err < best_err:
                best_err, best_idx = err, idx
        indices.append(best_idx)

    v = 0
    v |= (1 << 6)  # mode 6: 6 нулевых бит + единица на позиции 6
    pos = 7

    for c in range(4):  # R,G,B,A: (endpoint0, endpoint1) по 7 бит каждое
        v |= (raw0[c] & 0x7F) << pos; pos += 7
        v |= (raw1[c] & 0x7F) << pos; pos += 7

    v |= (pbit0 & 1) << pos; pos += 1
    v |= (pbit1 & 1) << pos; pos += 1

    for i, idx in enumerate(indices):
        nb = 3 if i == 0 else 4
        v |= (idx & ((1 << nb) - 1)) << pos
        pos += nb

    assert pos == 128, f"ожидалось 128 бит, получилось {pos}"
    return v.to_bytes(16, "little")


def encode_bc7_image(img: Image.Image) -> bytes:
    img = img.convert("RGBA")
    w, h = img.size
    bw, bh = (w + 3) // 4, (h + 3) // 4

    # паддинг картинки до кратности 4 (повторяем краевые пиксели)
    padded = Image.new("RGBA", (bw * 4, bh * 4))
    padded.paste(img, (0, 0))
    if bw * 4 > w:
        edge = padded.crop((w - 1, 0, w, bh * 4))
        for x in range(w, bw * 4):
            padded.paste(edge, (x, 0))
    if bh * 4 > h:
        edge = padded.crop((0, h - 1, bw * 4, h))
        for y in range(h, bh * 4):
            padded.paste(edge, (0, y))

    px = padded.load()
    out = bytearray()
    for by in range(bh):
        for bx in range(bw):
            block = [px[bx * 4 + (i % 4), by * 4 + (i // 4)] for i in range(16)]
            out += encode_block_mode6(block)
    return bytes(out)


def repack_ktx(new_image_path, original_ktx_path, out_path):
    with open(original_ktx_path, "rb") as f:
        orig = f.read()

    kvsize = struct.unpack_from("<I", orig, 16 + 44)[0]  # bytesOfKeyValueData - последнее поле заголовка
    header_len = 16 + 48 + kvsize
    header = bytearray(orig[:header_len])

    img = Image.open(new_image_path)
    w, h = img.size

    struct.pack_into("<I", header, 16 + 20, w)   # pixelWidth (поле #5 -> offset 16+4*5)
    struct.pack_into("<I", header, 16 + 24, h)   # pixelHeight

    image_data = encode_bc7_image(img)

    with open(out_path, "wb") as f:
        f.write(header)
        f.write(struct.pack("<I", len(image_data)))
        f.write(image_data)

    print(f"Сохранено: {out_path}")
    print(f"Размер: {len(header) + 4 + len(image_data):,} байт")
    print(f"Текстура: {w}x{h}, image_data={len(image_data):,} байт")


if __name__ == "__main__":
    if len(sys.argv) != 4:
        print("Использование: python3 ktx_bc7_encoder.py input.png original.ktx output.ktx")
        sys.exit(1)
    repack_ktx(sys.argv[1], sys.argv[2], sys.argv[3])
