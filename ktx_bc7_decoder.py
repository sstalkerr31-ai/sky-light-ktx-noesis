#!/usr/bin/env python3
"""
KTX v1 + BC7 decoder (для текстур Sky: Children of the Light)
================================================================
KTX - открытый формат Khronos, парсится по официальной спецификации
(без гаданий, всё 100% детерминировано).

BC7 - открытый формат блочного сжатия (Microsoft/Khronos BPTC).
Здесь реализовано:
  - Моды 4, 5, 6 (единственный subset, без partition) - декодируются
    ТОЧНО, побитово по спецификации.
  - Моды 0, 1, 2, 3, 7 (multi-subset, с partition-таблицами) -
    декодируются ПРИБЛИЖЁННО: берём среднее значение по всем endpoint'ам
    блока и заливаем плоским цветом весь блок 4x4. Это не даёт точной
    картинки на границах сложных областей, но передаёт общий цвет/форму -
    partition-таблицы (64 x 16 значений) я сознательно не стал тащить,
    чтобы не гадать по памяти и не выдавать потенциально ошибочные данные.
"""
import struct
import sys
from PIL import Image


WEIGHTS = {
    2: [0, 21, 43, 64],
    3: [0, 9, 18, 27, 37, 46, 55, 64],
    4: [0, 4, 9, 13, 17, 21, 26, 30, 34, 38, 43, 47, 51, 55, 60, 64],
}

MODE_PARAMS = {
    0: dict(NS=3, PB=4, RB=0, ISB=0, CB=4, AB=0, EPB=1, SPB=0, IB=3),
    1: dict(NS=2, PB=6, RB=0, ISB=0, CB=6, AB=0, EPB=0, SPB=1, IB=3),
    2: dict(NS=3, PB=6, RB=0, ISB=0, CB=5, AB=0, EPB=0, SPB=0, IB=2),
    3: dict(NS=2, PB=6, RB=0, ISB=0, CB=7, AB=0, EPB=1, SPB=0, IB=2),
    4: dict(NS=1, PB=0, RB=2, ISB=1, CB=5, AB=6, EPB=0, SPB=0, IB=2, IB2=3),
    5: dict(NS=1, PB=0, RB=2, ISB=0, CB=7, AB=8, EPB=0, SPB=0, IB=2, IB2=2),
    6: dict(NS=1, PB=0, RB=0, ISB=0, CB=7, AB=7, EPB=1, SPB=0, IB=4),
    7: dict(NS=2, PB=6, RB=0, ISB=0, CB=5, AB=5, EPB=1, SPB=0, IB=2),
}


def expand_bits(val, bits):
    """Расширяет val (bits точность) до полных 8 бит методом bit-replication."""
    if bits >= 8:
        return val & 0xFF
    val <<= (8 - bits)
    return (val | (val >> bits)) & 0xFF


def interpolate(e0, e1, weight):
    return tuple(((64 - weight) * a + weight * b + 32) >> 6 for a, b in zip(e0, e1))


def apply_rotation(rgba, rot):
    r, g, b, a = rgba
    if rot == 1:
        return (a, g, b, r)
    if rot == 2:
        return (r, a, b, g)
    if rot == 3:
        return (r, g, a, b)
    return (r, g, b, a)


class BitReader:
    def __init__(self, block16):
        self.v = int.from_bytes(block16, "little")
        self.pos = 0

    def read(self, n):
        if n == 0:
            return 0
        val = (self.v >> self.pos) & ((1 << n) - 1)
        self.pos += n
        return val


def decode_block_exact_ns1(br, mode, params):
    """Точный декод для NS=1 (моды 4,5,6)."""
    rot = br.read(params["RB"]) if params["RB"] else 0
    isb = br.read(1) if params["ISB"] else 0

    CB, AB = params["CB"], params["AB"]
    # endpoints: R0,R1,G0,G1,B0,B1,[A0,A1]
    comps_raw = [[br.read(CB) for _ in range(2)] for _ in range(3)]
    if AB > 0:
        comps_raw.append([br.read(AB) for _ in range(2)])
    else:
        comps_raw.append([255, 255])  # непрозрачно

    pbits = [0, 0]
    if params["EPB"]:
        pbits = [br.read(1), br.read(1)]

    def endpoint(e_idx):
        out = []
        for ci, raw_pair in enumerate(comps_raw):
            bits = CB if ci < 3 else AB
            v = raw_pair[e_idx]
            if params["EPB"]:
                v = (v << 1) | pbits[e_idx]
                bits += 1
            out.append(expand_bits(v, bits) if bits < 8 else (v & 0xFF))
        return tuple(out)  # (R,G,B,A)

    e0, e1 = endpoint(0), endpoint(1)

    IB = params["IB"]
    IB2 = params.get("IB2")

    pixels = [None] * 16

    if IB2 is None:
        # единый индекс на цвет+альфа (mode 6)
        for i in range(16):
            nb = IB if i != 0 else IB - 1
            idx = br.read(nb)
            w = WEIGHTS[IB][idx]
            pixels[i] = interpolate(e0, e1, w)
    else:
        # раздельные индексы (mode 4,5) - сначала "массив0"(IB), потом "массив1"(IB2)
        idx0 = []
        for i in range(16):
            nb = IB if i != 0 else IB - 1
            idx0.append(br.read(nb))
        idx1 = []
        for i in range(16):
            nb = IB2 if i != 0 else IB2 - 1
            idx1.append(br.read(nb))

        if isb == 0:
            color_idx, color_bits = idx0, IB
            alpha_idx, alpha_bits = idx1, IB2
        else:
            color_idx, color_bits = idx1, IB2
            alpha_idx, alpha_bits = idx0, IB

        for i in range(16):
            wc = WEIGHTS[color_bits][color_idx[i]]
            wa = WEIGHTS[alpha_bits][alpha_idx[i]]
            rgb = interpolate(e0[:3], e1[:3], wc)
            a = interpolate(e0[3:4], e1[3:4], wa)[0]
            pixels[i] = (*rgb, a)

    if rot:
        pixels = [apply_rotation(p, rot) for p in pixels]

    return pixels


def decode_block_approx_multisubset(br, mode, params):
    """Приближённый декод для NS=2/3: пропускаем partition/rotation/ISB,
    читаем все endpoint-компоненты и усредняем -> заливаем блок плоским цветом."""
    br.read(params["PB"])
    br.read(params["RB"])
    if params["ISB"]:
        br.read(1)

    NS = params["NS"]
    CB, AB = params["CB"], params["AB"]

    sums = [0, 0, 0, 0]
    counts = [0, 0, 0, 0]
    for ci, bits in enumerate((CB, CB, CB, AB if AB else 0)):
        if bits == 0:
            continue
        for _s in range(NS):
            for _e in range(2):
                v = br.read(bits)
                sums[ci] += expand_bits(v, bits)
                counts[ci] += 1

    r = sums[0] // counts[0] if counts[0] else 0
    g = sums[1] // counts[1] if counts[1] else 0
    b = sums[2] // counts[2] if counts[2] else 0
    a = sums[3] // counts[3] if counts[3] else 255

    return [(r, g, b, a)] * 16


def decode_bc7_block(block16):
    br = BitReader(block16)
    mode = -1
    for i in range(8):
        if (br.v >> i) & 1:
            mode = i
            break
    if mode == -1:
        return [(0, 0, 0, 255)] * 16

    br.pos = mode + 1
    params = MODE_PARAMS[mode]

    if params["NS"] == 1:
        return decode_block_exact_ns1(br, mode, params)
    else:
        return decode_block_approx_multisubset(br, mode, params)


def parse_ktx(path):
    with open(path, "rb") as f:
        data = f.read()

    identifier = data[0:12]
    expected = bytes([0xAB, ord('K'), ord('T'), ord('X'), ord(' '),
                       ord('1'), ord('1'), 0xBB, 0x0D, 0x0A, 0x1A, 0x0A])
    if identifier != expected:
        raise ValueError("Не похоже на KTX v1 файл")

    fields = struct.unpack_from("<12I", data, 16)
    names = ['glType', 'glTypeSize', 'glFormat', 'glInternalFormat',
              'glBaseInternalFormat', 'pixelWidth', 'pixelHeight', 'pixelDepth',
              'numberOfArrayElements', 'numberOfFaces', 'numberOfMipmapLevels',
              'bytesOfKeyValueData']
    header = dict(zip(names, fields))

    offset = 16 + 48 + header['bytesOfKeyValueData']
    image_size = struct.unpack_from("<I", data, offset)[0]
    offset += 4
    image_data = data[offset:offset + image_size]

    return header, image_data


def decode_bc7_image(image_data, width, height):
    blocks_x = (width + 3) // 4
    blocks_y = (height + 3) // 4

    out = Image.new("RGBA", (blocks_x * 4, blocks_y * 4))
    px = out.load()

    pos = 0
    for by in range(blocks_y):
        for bx in range(blocks_x):
            block = image_data[pos:pos + 16]
            pos += 16
            pixels = decode_bc7_block(block)
            for i, (r, g, b, a) in enumerate(pixels):
                x = bx * 4 + (i % 4)
                y = by * 4 + (i // 4)
                px[x, y] = (r, g, b, a)

    return out.crop((0, 0, width, height))


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else "logo.ktx"
    out_path = sys.argv[2] if len(sys.argv) > 2 else "logo_decoded.png"

    header, image_data = parse_ktx(path)
    print("KTX заголовок:", header)
    print(f"Размер данных изображения: {len(image_data):,} байт")

    img = decode_bc7_image(image_data, header['pixelWidth'], header['pixelHeight'])
    img.save(out_path)
    print(f"Сохранено: {out_path}")
