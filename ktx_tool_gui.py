#!/usr/bin/env python3
"""
KTX Texture Tool - простое GUI для личного использования
============================================================
Decode: выбрал .ktx -> увидел превью -> сам решил, сохранять ли PNG и куда.
Encode: выбрал новую картинку + оригинальный .ktx (для формата/заголовка)
        -> сам решил, куда сохранить результат (Save As, никакой автозаписи).

Требования: Python 3.8+, Pillow (pip install Pillow --break-system-packages
если ругается на "externally managed environment").

Запуск: python3 ktx_tool_gui.py
"""
import struct
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from PIL import Image, ImageTk
import io


# ============================================================
#  BC7 / KTX декодер и энкодер (та же логика, что мы отладили раньше)
# ============================================================

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

KTX_IDENTIFIER = bytes([0xAB, ord('K'), ord('T'), ord('X'), ord(' '),
                         ord('1'), ord('1'), 0xBB, 0x0D, 0x0A, 0x1A, 0x0A])


def expand_bits(val, bits):
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


def decode_block_exact_ns1(br, params):
    rot = br.read(params["RB"]) if params["RB"] else 0
    isb = br.read(1) if params["ISB"] else 0
    CB, AB = params["CB"], params["AB"]
    comps_raw = [[br.read(CB) for _ in range(2)] for _ in range(3)]
    if AB > 0:
        comps_raw.append([br.read(AB) for _ in range(2)])
    else:
        comps_raw.append([255, 255])
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
        return tuple(out)

    e0, e1 = endpoint(0), endpoint(1)
    IB = params["IB"]
    IB2 = params.get("IB2")
    pixels = [None] * 16

    if IB2 is None:
        for i in range(16):
            nb = IB if i != 0 else IB - 1
            idx = br.read(nb)
            w = WEIGHTS[IB][idx]
            pixels[i] = interpolate(e0, e1, w)
    else:
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


def decode_block_approx_multisubset(br, params):
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
        return decode_block_exact_ns1(br, params)
    else:
        return decode_block_approx_multisubset(br, params)


def parse_ktx(path):
    with open(path, "rb") as f:
        data = f.read()
    if data[0:12] != KTX_IDENTIFIER:
        raise ValueError("Не похоже на KTX v1 файл (неверная сигнатура)")
    fields = struct.unpack_from("<12I", data, 16)
    names = ['glType', 'glTypeSize', 'glFormat', 'glInternalFormat',
              'glBaseInternalFormat', 'pixelWidth', 'pixelHeight', 'pixelDepth',
              'numberOfArrayElements', 'numberOfFaces', 'numberOfMipmapLevels',
              'bytesOfKeyValueData']
    header = dict(zip(names, fields))
    if header['glInternalFormat'] not in (0x8E8D, 0x8E8C):
        raise ValueError(f"Формат 0x{header['glInternalFormat']:X} не BC7 - декодер поддерживает только BC7")
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


# ---------- энкодер (mode 6) ----------

WEIGHTS4 = WEIGHTS[4]


def encode_endpoint(vals4):
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
    return best[1], best[2]


def encode_block_mode6(block_pixels):
    e0f = tuple(min(p[c] for p in block_pixels) for c in range(4))
    e1f = tuple(max(p[c] for p in block_pixels) for c in range(4))
    pbit0, raw0 = encode_endpoint(e0f)
    pbit1, raw1 = encode_endpoint(e1f)
    e0 = tuple(r * 2 + pbit0 for r in raw0)
    e1 = tuple(r * 2 + pbit1 for r in raw1)

    indices = []
    for i, p in enumerate(block_pixels):
        best_idx, best_err = 0, None
        rng = range(8) if i == 0 else range(16)
        for idx in rng:
            w = WEIGHTS4[idx]
            interp = interpolate(e0, e1, w)
            err = sum((a - b) ** 2 for a, b in zip(interp, p))
            if best_err is None or err < best_err:
                best_err, best_idx = err, idx
        indices.append(best_idx)

    v = 0
    v |= (1 << 6)
    pos = 7
    for c in range(4):
        v |= (raw0[c] & 0x7F) << pos; pos += 7
        v |= (raw1[c] & 0x7F) << pos; pos += 7
    v |= (pbit0 & 1) << pos; pos += 1
    v |= (pbit1 & 1) << pos; pos += 1
    for i, idx in enumerate(indices):
        nb = 3 if i == 0 else 4
        v |= (idx & ((1 << nb) - 1)) << pos
        pos += nb
    return v.to_bytes(16, "little")


def encode_bc7_image(img: Image.Image) -> bytes:
    img = img.convert("RGBA")
    w, h = img.size
    bw, bh = (w + 3) // 4, (h + 3) // 4
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


def repack_ktx_bytes(new_image: Image.Image, original_ktx_path: str) -> bytes:
    with open(original_ktx_path, "rb") as f:
        orig = f.read()
    kvsize = struct.unpack_from("<I", orig, 16 + 44)[0]
    header_len = 16 + 48 + kvsize
    header = bytearray(orig[:header_len])
    w, h = new_image.size
    struct.pack_into("<I", header, 16 + 20, w)
    struct.pack_into("<I", header, 16 + 24, h)
    image_data = encode_bc7_image(new_image)
    return bytes(header) + struct.pack("<I", len(image_data)) + image_data


# ============================================================
#  GUI
# ============================================================

class KtxToolApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("KTX Texture Tool - для личного использования")
        self.geometry("900x650")
        self.resizable(True, True)

        self.decoded_image = None       # PIL.Image после декодирования
        self.decode_source_path = None

        self.new_image = None           # PIL.Image для энкодинга (новая текстура)
        self.new_image_path = None
        self.encode_original_ktx_path = None
        self.encoded_preview = None      # PIL.Image после кодирования (для контроля качества)

        notebook = ttk.Notebook(self)
        notebook.pack(fill="both", expand=True, padx=8, pady=8)

        self.decode_tab = ttk.Frame(notebook)
        self.encode_tab = ttk.Frame(notebook)
        notebook.add(self.decode_tab, text="Decode (.ktx -> PNG)")
        notebook.add(self.encode_tab, text="Encode (PNG -> .ktx)")

        self._build_decode_tab()
        self._build_encode_tab()

    # ---------------- DECODE TAB ----------------
    def _build_decode_tab(self):
        top = ttk.Frame(self.decode_tab)
        top.pack(fill="x", pady=8, padx=8)

        ttk.Button(top, text="Открыть .ktx...", command=self.on_open_ktx).pack(side="left")
        self.decode_path_label = ttk.Label(top, text="Файл не выбран")
        self.decode_path_label.pack(side="left", padx=10)

        self.decode_info_label = ttk.Label(self.decode_tab, text="")
        self.decode_info_label.pack(anchor="w", padx=8)

        self.decode_canvas = tk.Label(self.decode_tab, text="Превью появится здесь",
                                       relief="groove", background="#222222", foreground="#aaaaaa")
        self.decode_canvas.pack(fill="both", expand=True, padx=8, pady=8)

        bottom = ttk.Frame(self.decode_tab)
        bottom.pack(fill="x", pady=8, padx=8)
        ttk.Button(bottom, text="Save as PNG...", command=self.on_save_png).pack(side="left")

    def on_open_ktx(self):
        path = filedialog.askopenfilename(
            title="Выбери .ktx файл",
            filetypes=[("KTX texture", "*.ktx"), ("Все файлы", "*.*")]
        )
        if not path:
            return
        try:
            header, image_data = parse_ktx(path)
            if header['numberOfMipmapLevels'] > 1 or header['numberOfFaces'] > 1 or header['numberOfArrayElements'] > 0:
                messagebox.showwarning(
                    "Не поддерживается",
                    "Этот .ktx содержит мипмапы/кубмапу/массив слоёв.\n"
                    "Инструмент поддерживает только простые 2D BC7-текстуры."
                )
                return
            w, h = header['pixelWidth'], header['pixelHeight']
            img = decode_bc7_image(image_data, w, h)
            self.decoded_image = img
            self.decode_source_path = path

            self.decode_path_label.config(text=path)
            self.decode_info_label.config(text=f"{w}x{h}  |  BC7  |  {len(image_data):,} байт данных изображения")

            self._show_preview(self.decode_canvas, img)
        except Exception as e:
            messagebox.showerror("Ошибка декодирования", f"Не удалось декодировать файл:\n\n{e}")

    def on_save_png(self):
        if self.decoded_image is None:
            messagebox.showinfo("Нечего сохранять", "Сначала открой и декодируй .ktx файл.")
            return
        path = filedialog.asksaveasfilename(
            title="Сохранить как PNG",
            defaultextension=".png",
            filetypes=[("PNG image", "*.png")]
        )
        if not path:
            return
        try:
            self.decoded_image.save(path)
            messagebox.showinfo("Готово", f"Сохранено:\n{path}")
        except Exception as e:
            messagebox.showerror("Ошибка сохранения", f"Не удалось сохранить файл:\n\n{e}")

    # ---------------- ENCODE TAB ----------------
    def _build_encode_tab(self):
        top = ttk.Frame(self.encode_tab)
        top.pack(fill="x", pady=8, padx=8)

        ttk.Button(top, text="1. Оригинальный .ktx (для формата)...",
                   command=self.on_pick_original_ktx).pack(side="left")
        self.encode_orig_label = ttk.Label(top, text="Не выбран")
        self.encode_orig_label.pack(side="left", padx=10)

        top2 = ttk.Frame(self.encode_tab)
        top2.pack(fill="x", pady=4, padx=8)
        ttk.Button(top2, text="2. Новая картинка (PNG/JPG)...",
                   command=self.on_pick_new_image).pack(side="left")
        self.encode_img_label = ttk.Label(top2, text="Не выбрана")
        self.encode_img_label.pack(side="left", padx=10)

        mid = ttk.Frame(self.encode_tab)
        mid.pack(fill="x", pady=8, padx=8)
        ttk.Button(mid, text="3. Encode (предпросмотр)", command=self.on_encode).pack(side="left")

        self.encode_canvas = tk.Label(self.encode_tab, text="Превью результата появится здесь",
                                       relief="groove", background="#222222", foreground="#aaaaaa")
        self.encode_canvas.pack(fill="both", expand=True, padx=8, pady=8)

        bottom = ttk.Frame(self.encode_tab)
        bottom.pack(fill="x", pady=8, padx=8)
        ttk.Button(bottom, text="4. Save as .ktx...", command=self.on_save_ktx).pack(side="left")

        self._encoded_bytes = None

    def on_pick_original_ktx(self):
        path = filedialog.askopenfilename(
            title="Выбери оригинальный .ktx (берём оттуда формат/заголовок)",
            filetypes=[("KTX texture", "*.ktx"), ("Все файлы", "*.*")]
        )
        if not path:
            return
        self.encode_original_ktx_path = path
        self.encode_orig_label.config(text=path)

    def on_pick_new_image(self):
        path = filedialog.askopenfilename(
            title="Выбери новую картинку",
            filetypes=[("Изображения", "*.png *.jpg *.jpeg *.bmp"), ("Все файлы", "*.*")]
        )
        if not path:
            return
        try:
            self.new_image = Image.open(path)
            self.new_image_path = path
            self.encode_img_label.config(text=f"{path}  ({self.new_image.size[0]}x{self.new_image.size[1]})")
        except Exception as e:
            messagebox.showerror("Ошибка", f"Не удалось открыть картинку:\n\n{e}")

    def on_encode(self):
        if not self.encode_original_ktx_path:
            messagebox.showinfo("Не хватает данных", "Сначала выбери оригинальный .ktx файл (шаг 1).")
            return
        if self.new_image is None:
            messagebox.showinfo("Не хватает данных", "Сначала выбери новую картинку (шаг 2).")
            return
        try:
            self._encoded_bytes = repack_ktx_bytes(self.new_image, self.encode_original_ktx_path)

            # раскодируем обратно, чтобы честно показать, как это будет выглядеть в игре
            header, image_data = parse_ktx_from_bytes(self._encoded_bytes)
            preview = decode_bc7_image(image_data, header['pixelWidth'], header['pixelHeight'])
            self.encoded_preview = preview
            self._show_preview(self.encode_canvas, preview)
            messagebox.showinfo(
                "Готово",
                f"Закодировано: {header['pixelWidth']}x{header['pixelHeight']}, "
                f"{len(self._encoded_bytes):,} байт.\n\n"
                "Превью выше показывает результат ПОСЛЕ сжатия BC7 (как оно будет выглядеть по-настоящему)."
            )
        except Exception as e:
            messagebox.showerror("Ошибка кодирования", f"Не удалось закодировать:\n\n{e}")

    def on_save_ktx(self):
        if self._encoded_bytes is None:
            messagebox.showinfo("Нечего сохранять", "Сначала нажми 'Encode' (шаг 3).")
            return
        path = filedialog.asksaveasfilename(
            title="Сохранить как .ktx",
            defaultextension=".ktx",
            filetypes=[("KTX texture", "*.ktx")]
        )
        if not path:
            return
        try:
            with open(path, "wb") as f:
                f.write(self._encoded_bytes)
            messagebox.showinfo("Готово", f"Сохранено:\n{path}\n\nТы сам решаешь, куда положить этот файл дальше.")
        except Exception as e:
            messagebox.showerror("Ошибка сохранения", f"Не удалось сохранить файл:\n\n{e}")

    # ---------------- общие утилиты ----------------
    def _show_preview(self, label_widget, pil_image):
        img = pil_image.copy()
        img.thumbnail((800, 450))
        tk_img = ImageTk.PhotoImage(img)
        label_widget.configure(image=tk_img, text="")
        label_widget.image = tk_img  # держим ссылку, иначе Tkinter соберёт мусор


def parse_ktx_from_bytes(data: bytes):
    if data[0:12] != KTX_IDENTIFIER:
        raise ValueError("Не похоже на KTX v1 (неверная сигнатура)")
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


if __name__ == "__main__":
    app = KtxToolApp()
    app.mainloop()
