"""
图片拼接工具 v8
- 缩放图像缓存：resize只执行一次，拖动时只做快速crop → 大幅提速
- 拖动时冻结布局，不重算offset/scrollregion → 消除"整体位移"感
- 深色模式：完整递归主题应用
- 真实裁剪版：拖动边缘时直接缩减对应边缘的可见内容，不再是平移取景
- 修正裁剪变形：裁掉后同步缩减该分块尺寸，不再把裁后内容强行拉伸回原区域
- PDF导出无白边
- 适应窗口、预览、增加拼接线等功能完整保留
"""
from __future__ import annotations

import io
import queue
import tempfile
import threading
from datetime import datetime
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import tkinter as tk
from tkinter import colorchooser, filedialog, messagebox, simpledialog, ttk
from tkinter import font as tkfont
try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
except Exception:
    DND_FILES = "DND_Files"
    TkinterDnD = None
from PIL import Image, ImageDraw, ImageFont, ImageOps, ImageTk

# 图片工具必须能处理高分辨率大图（手机全景、超长截图可超过 1 亿像素）。
# 放开 PIL 默认 8900 万像素上限，避免 DecompressionBomb 警告/报错。
Image.MAX_IMAGE_PIXELS = None
import math
import sys

try:
    import ctypes
except Exception:
    ctypes = None


# ── Windows 高分屏适配 ─────────────────────────────────────────────
def _enable_dpi_awareness() -> None:
    """声明进程 DPI 感知，避免 tkinter 被系统按位图放大导致界面模糊。

    必须在创建任何窗口之前调用（模块导入时调用一次即可）。
    """
    if sys.platform != "win32" or ctypes is None:
        return
    try:
        # Per-Monitor V2（Win10 1703+）：多显示器不同缩放也保持清晰
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        return
    except Exception:
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # Per-Monitor V1
        return
    except Exception:
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()  # 系统 DPI 感知（旧系统）
    except Exception:
        pass


def _get_system_dpi() -> float:
    """返回系统逻辑 DPI（默认 96）。"""
    if sys.platform != "win32" or ctypes is None:
        return 96.0
    try:
        dpi = int(ctypes.windll.user32.GetDpiForSystem())
        if dpi > 0:
            return float(dpi)
    except Exception:
        pass
    return 96.0


def _get_primary_scale() -> float:
    """主显示器（启动基准）UI 缩放系数：125% → 1.25，200% → 2.0。"""
    if sys.platform != "win32" or ctypes is None:
        return 1.0
    try:
        # GetScaleFactorForDevice 直接把缩放百分比作为返回值（如 125）
        pct = int(ctypes.windll.shcore.GetScaleFactorForDevice(0))
        if pct > 0:
            return pct / 100.0
    except Exception:
        pass
    try:
        dpi = int(ctypes.windll.user32.GetDpiForSystem())
        if dpi > 0:
            return dpi / 96.0
    except Exception:
        pass
    return 1.0


def _get_window_dpi(hwnd: Optional[int] = None) -> int:
    """返回窗口所在显示器当前生效的 DPI（96 / 120 / 144 / 192 ...）。

    进程已声明 Per-Monitor V2 DPI 感知，GetDpiForWindow 返回的是窗口
    所在显示器含系统缩放的 DPI，比启动时固定的主显示器数值更准确。
    """
    if sys.platform != "win32" or ctypes is None:
        return 0
    try:
        if hwnd:
            dpi = int(ctypes.windll.user32.GetDpiForWindow(hwnd))
            if dpi > 0:
                return dpi
    except Exception:
        pass
    try:
        dpi = int(ctypes.windll.user32.GetDpiForSystem())
        if dpi > 0:
            return dpi
    except Exception:
        pass
    return 0


def _get_window_scale(hwnd: Optional[int] = None) -> float:
    """按窗口所在显示器的实际 DPI 返回 UI 缩放系数（125% → 1.25）。"""
    dpi = _get_window_dpi(hwnd)
    if dpi > 0:
        return dpi / 96.0
    return _get_primary_scale()


_enable_dpi_awareness()
UI_SCALE = _get_primary_scale()


def ui(v: float) -> int:
    """把设计稿像素按系统缩放换算为实际像素。"""
    return max(1, int(round(v * UI_SCALE)))


def _refresh_ui_constants() -> None:
    """按当前 UI_SCALE 重算依赖缩放的模块常量（跨显示器 DPI 变化时调用）。"""
    global MARGIN, THUMB_SIZE
    MARGIN = ui(52)
    THUMB_SIZE = (ui(180), ui(180))


APP_NAME = "PicaCrop 图片拼接"
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff"}
THUMB_SIZE = (ui(180), ui(180))
NAME_BAR = ui(24)   # 缩略图下方文件名条高度
MARGIN = ui(52)
INTERNAL_ZOOM = 1.15   # how much larger than frame the source is rendered (pan headroom)
RENDER_DELAY = 8       # ms – debounce between renders (~55 fps max)

THEMES: Dict[str, Dict[str, str]] = {
    "light": {
        "bg": "#f2f3f5",
        "panel": "#ffffff",
        "panel2": "#f5f6f8",
        "canvas": "#e8eaef",
        "fg": "#1f2329",
        "muted": "#8a919f",
        "border": "#e4e6eb",
        "blue": "#3370ff",
        "blue_soft": "#6b95ff",
        "blue_dark": "#2557d6",
        "yellow": "#f7b500",
        "yellow_dark": "#8a5a0b",
        "ok": "#00b578",
        "danger": "#f53f3f",
        "shadow": "#dfe2e8",
        "hero": "#eef4ff",
    },
    "dark": {
        "bg": "#14161a",
        "panel": "#1c1f24",
        "panel2": "#262a31",
        "canvas": "#0e1013",
        "fg": "#e8eaed",
        "muted": "#8b919c",
        "border": "#33373f",
        "blue": "#4d7fff",
        "blue_soft": "#8fadff",
        "blue_dark": "#2b5bd9",
        "yellow": "#f7b500",
        "yellow_dark": "#8a5a0b",
        "ok": "#2fc983",
        "danger": "#f2555a",
        "shadow": "#0a0c0f",
        "hero": "#1a2540",
    },
}

def clamp(v, lo, hi):
    return max(lo, min(hi, v))


def _rounded_rect_points(x0: float, y0: float, x1: float, y1: float, r: float):
    """生成圆角矩形的多边形采样点，配合 canvas smooth=True 得到平滑圆角。"""
    r = max(1.0, min(float(r), (x1 - x0) / 2, (y1 - y0) / 2))
    steps = 8
    pts = []

    def arc(cx, cy, start, end):
        for i in range(steps + 1):
            a = math.radians(start + (end - start) * i / steps)
            pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))

    arc(x0 + r, y0 + r, 180, 270)   # 左上
    arc(x1 - r, y0 + r, 270, 360)   # 右上
    arc(x1 - r, y1 - r, 0, 90)      # 右下
    arc(x0 + r, y1 - r, 90, 180)    # 左下
    return pts


# ── Picsew 式自动拼接：重叠区域自动检测 ────────────────────────────────
def _gray_strip(img: Image.Image, width: int = 72) -> List[List[int]]:
    """只压缩宽度、保留行数，返回灰度行向量（用于重叠检测）。"""
    g = img.convert("L")
    w = min(width, g.width)
    if w != g.width:
        g = g.resize((w, g.height), Image.Resampling.BOX)
    px = g.load()
    return [[px[x, y] for x in range(w)] for y in range(g.height)]


def _row_ssd(ra: List[int], rb: List[int]) -> int:
    s = 0
    for x, y in zip(ra, rb):
        d = x - y
        s += d * d
    return s


def _numpy_diag_costs(np, rows_a, rows_b, w, sb, min_ov, max_ov):
    """用 FFT 快速计算各重叠量下的平均误差（对角线求和）。"""
    A = np.asarray(rows_a, dtype=np.float64)
    B = np.asarray(rows_b, dtype=np.float64)
    hA = A.shape[0]
    PA = np.concatenate([[0.0], np.cumsum(np.sum(A * A, axis=1))])
    PB = np.concatenate([[0.0], np.cumsum(np.sum(B * B, axis=1))])
    Bm = B.copy()
    Bm[:sb] = 0.0  # 状态栏区域不参与匹配
    n = hA
    conv = np.fft.irfft(
        np.fft.rfft(A, n=2 * n, axis=0) * np.fft.rfft(Bm[::-1], n=2 * n, axis=0),
        n=2 * n, axis=0)
    delta0 = max(0, hA - sb - max_ov)
    delta1 = hA - sb - min_ov
    deltas = np.arange(delta0, delta1 + 1)
    if len(deltas) == 0:
        return None
    c = conv[n - 1 + deltas].sum(axis=1)
    Asq = PA[hA] - PA[deltas + sb]
    Bsq = PB[hA - deltas] - PB[sb]
    ssd = Asq + Bsq - 2.0 * c
    ovs = hA - sb - deltas
    costs = ssd / (np.maximum(1, ovs) * w)
    i = int(np.argmin(costs))
    return float(costs[i]), int(ovs[i]), float(costs.mean())


def _pure_diag_costs(rows_a, rows_b, w, sb, min_ov, max_ov, target_h=240):
    """无 numpy 时的粗略回退：把行数降到 target_h 后直接暴力求对角线。"""
    hA = len(rows_a)
    if hA <= 0:
        return None

    def sample(rows):
        h = len(rows)
        if h <= target_h:
            return rows
        out = []
        for t in range(target_h):
            out.append(rows[int(t * h / target_h)])
        return out

    sa = sample(rows_a)
    sb_ = sample(rows_b)
    h = len(sa)
    if h < 100 or len(sb_) != h:
        return None
    sb2 = int(sb * h / hA)
    min_ov_s = max(1, int(min_ov * h / hA))
    max_ov_s = min(h - sb2 - 12, int(max_ov * h / hA))
    if max_ov_s < min_ov_s:
        return None
    costs = []
    best = None
    for ov in range(min_ov_s, max_ov_s + 1):
        ssd = 0
        for i in range(ov):
            ssd += _row_ssd(sa[h - ov + i], sb_[sb2 + i])
        cost = ssd / (ov * w)
        costs.append(cost)
        if best is None or cost < best[0] - 1e-9:
            best = (cost, ov)
    if best is None:
        return None
    cost, ov_s = best
    ov_orig = int(round(ov_s * hA / h))
    return float(cost), ov_orig, float(sum(costs) / len(costs))


def _detect_row_overlap(rows_a, rows_b, max_sb=180, min_ov=10, min_keep=36):
    """返回 (overlap_rows, status_bar_rows) 或 None（未找到可信重叠）。"""
    hA, hB = len(rows_a), len(rows_b)
    if hA < 120 or hB < 120:
        return None
    w = len(rows_a[0]) if rows_a else 0
    if w == 0:
        return None
    # 1) 公共前缀 = 固定状态栏高度
    sb = 0
    while sb < min(max_sb, hA, hB):
        if _row_ssd(rows_a[sb], rows_b[sb]) > w * 16:
            break
        sb += 1
    # 2) 若前缀不明显（状态栏可能不同），则尝试多个候选高度
    sb_candidates = [sb] if sb >= 8 else list(range(0, min(max_sb, hA, hB) + 1, 2))
    try:
        import numpy as np
    except Exception:
        np = None
    best = None
    for sb2 in sb_candidates:
        max_ov = min(hA - sb2 - min_keep, hB - sb2 - min_keep)
        if max_ov < min_ov:
            continue
        if np is not None:
            res = _numpy_diag_costs(np, rows_a, rows_b, w, sb2, min_ov, max_ov)
        else:
            res = _pure_diag_costs(rows_a, rows_b, w, sb2, min_ov, max_ov)
        if res is None:
            continue
        cost, ov, mean = res
        if best is None or cost < best[0] - 1e-9:
            best = (cost, ov, mean, sb2)
    if best is None:
        return None
    cost, ov, mean, sb2 = best
    # 置信度：误差足够低，且相对均值有明显“谷底”
    if cost > 80.0 or cost * 2.5 >= mean:
        return None
    return ov, sb2


def detect_stitch_overlap(img_a: Image.Image, img_b: Image.Image, orientation: str):
    """检测两张相邻截图的重叠，返回 (crop_a, crop_b) 裁剪比例字典或 None。"""
    try:
        if orientation == "horizontal":
            ta = img_a.transpose(Image.Transpose.ROTATE_270)
            tb = img_b.transpose(Image.Transpose.ROTATE_270)
            res = _detect_row_overlap(_gray_strip(ta), _gray_strip(tb))
            if res is None:
                return None
            ov, sb = res
            return ({"crop_right": ov / max(1, img_a.width)},
                    {"crop_left": sb / max(1, img_b.width)})
        res = _detect_row_overlap(_gray_strip(img_a), _gray_strip(img_b))
        if res is None:
            return None
        ov, sb = res
        return ({"crop_bottom": ov / max(1, img_a.height)},
                {"crop_top": sb / max(1, img_b.height)})
    except Exception:
        return None


def _render_export(full_sources, tiles, orientation, scale, bg_rgb) -> Image.Image:
    """纯函数：按原生分辨率渲染拼接结果（后台线程用，不触碰 tkinter）。

    逻辑与 _full_composite 等价，但基于传入的快照，导出期间界面仍可操作不卡顿。
    """
    dims = []
    for tile in tiles:
        src = full_sources[tile.source_idx]
        vis_w = max(0.0, 1.0 - tile.crop_left - tile.crop_right)
        vis_h = max(0.0, 1.0 - tile.crop_top - tile.crop_bottom)
        if vis_w <= 1e-9 or vis_h <= 1e-9:
            dims.append((0, 0))
        else:
            dims.append((int(round(src.width * vis_w)), int(round(src.height * vis_h))))
    if orientation == "horizontal":
        base_h = max((h for w, h in dims if h > 0), default=1)
        sizes = []
        for w, h in dims:
            if w <= 0 or h <= 0:
                sizes.append((0, 0))
            else:
                sizes.append((max(1, int(round(base_h * (w / max(1, h))))), base_h))
    else:
        base_w = max((w for w, h in dims if w > 0), default=1)
        sizes = []
        for w, h in dims:
            if w <= 0 or h <= 0:
                sizes.append((0, 0))
            else:
                sizes.append((base_w, max(1, int(round(base_w * (h / max(1, w)))))))
    positions = []
    if orientation == "horizontal":
        cx = 0
        for w, _h in sizes:
            positions.append((cx, 0))
            cx += w
    else:
        cy = 0
        for _w, h in sizes:
            positions.append((0, cy))
            cy += h
    min_x = min((x for x, _y in positions), default=0)
    min_y = min((y for _x, y in positions), default=0)
    max_x = max((x + w for (x, _y), (w, _h) in zip(positions, sizes)), default=1)
    max_y = max((y + h for (_x, y), (_w, h) in zip(positions, sizes)), default=1)
    shift_x = -min(0, min_x)
    shift_y = -min(0, min_y)
    out = Image.new("RGB", (max(1, int(max_x + shift_x)), max(1, int(max_y + shift_y))), bg_rgb)
    for i, tile in enumerate(tiles):
        fw, fh = sizes[i]
        if fw <= 0 or fh <= 0:
            continue
        src = full_sources[tile.source_idx]
        px, py = positions[i]
        left = int(src.width * clamp(tile.crop_left, 0.0, 1.0))
        right = int(src.width * (1.0 - clamp(tile.crop_right, 0.0, 1.0)))
        top = int(src.height * clamp(tile.crop_top, 0.0, 1.0))
        bottom = int(src.height * (1.0 - clamp(tile.crop_bottom, 0.0, 1.0)))
        if right <= left or bottom <= top:
            continue
        rendered = src.crop((left, top, right, bottom)).resize((fw, fh), Image.Resampling.LANCZOS)
        out.paste(rendered, (int(px + shift_x), int(py + shift_y)))
    if scale < 1.0:
        nw = max(1, int(round(out.width * scale)))
        nh = max(1, int(round(out.height * scale)))
        if (nw, nh) != out.size:
            out = out.resize((nw, nh), Image.Resampling.LANCZOS)
    return out


def _estimate_init_scale(sources, tiles, orientation, fmt, target_bytes) -> float:
    """按目标体积预估初始渲染比例：少渲染像素 = 导出更快。"""
    dims = []
    for tile in tiles:
        src = sources[tile.source_idx]
        vw = max(0.0, 1.0 - tile.crop_left - tile.crop_right)
        vh = max(0.0, 1.0 - tile.crop_top - tile.crop_bottom)
        if vw > 1e-9 and vh > 1e-9:
            dims.append((src.width * vw, src.height * vh))
    if not dims:
        return 1.0
    if orientation == "horizontal":
        base_h = max(h for w, h in dims)
        w_total = sum(max(1, base_h * (w / max(1, h))) for w, h in dims)
        px = w_total * base_h
    else:
        base_w = max(w for w, h in dims)
        h_total = sum(max(1, base_w * (h / max(1, w))) for w, h in dims)
        px = base_w * h_total
    if px <= 0:
        return 1.0
    if fmt == "jpeg":
        bpp = 0.22
    elif fmt == "webp":
        bpp = 0.16
    else:
        bpp = 0.9
    scale = math.sqrt(target_bytes / max(1.0, px * bpp))
    return clamp(scale, 0.05, 1.0)


def _pdf_bytes_from_jpeg(jpeg_bytes: bytes, width: int, height: int) -> bytes:
    """把 JPEG 字节打包成单页 PDF（内嵌 JPEG，文件大小可控）。"""
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []

    def obj(num, data):
        offsets.append(out.tell())
        out.write(f"{num} 0 obj\n".encode())
        out.write(data)
        out.write(b"\nendobj\n")

    obj(1, b"<< /Type /Catalog /Pages 2 0 R >>")
    obj(2, b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>")
    obj(3, (f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {width} {height}] "
            f"/Resources << /XObject << /Im0 4 0 R >> >> /Contents 5 0 R >>").encode())
    obj(4, (f"<< /Type /XObject /Subtype /Image /Width {width} /Height {height} "
            f"/ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /DCTDecode "
            f"/Length {len(jpeg_bytes)} >>\nstream\n").encode() + jpeg_bytes + b"\nendstream")
    content = f"q {width} 0 0 {height} 0 0 cm /Im0 Do Q".encode()
    obj(5, f"<< /Length {len(content)} >>\nstream\n".encode() + content + b"\nendstream")
    xref_pos = out.tell()
    out.write(b"xref\n0 6\n")
    out.write(b"0000000000 65535 f \n")
    for off in offsets:
        out.write(f"{off:010d} 00000 n \n".encode())
    out.write(b"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n")
    out.write(f"{xref_pos}\n".encode())
    out.write(b"%%EOF")
    return out.getvalue()


def _save_to_target(composite, fmt, out_path, target_bytes, jpeg_hint):
    """按目标文件大小保存：JPG/PDF 调质量逼近；PNG 超目标则降分辨率。

    全部在后台线程运行，不触碰 tkinter。
    """
    tol = 0.18
    if fmt in ("jpeg", "webp", "pdf"):
        q = int(jpeg_hint)
        rgb0 = composite.convert("RGB")
        img = rgb0
        scale = 1.0
        for _ in range(10):
            if fmt == "jpeg":
                img.save(out_path, "JPEG", quality=q, optimize=True, progressive=True)
            elif fmt == "webp":
                # WebP 单边最大 16383px，超出先等比缩小
                ww, hh = img.size
                mx = max(ww, hh)
                if mx > 16383:
                    s = 16383 / mx
                    img = img.resize((max(1, int(ww * s)), max(1, int(hh * s))),
                                     Image.Resampling.LANCZOS)
                img.save(out_path, "WEBP", quality=q, method=3)
            else:
                buf = io.BytesIO()
                img.save(buf, "JPEG", quality=q, optimize=True, progressive=True)
                out_path.write_bytes(_pdf_bytes_from_jpeg(buf.getvalue(), img.width, img.height))
            size = out_path.stat().st_size
            if size <= 0:
                return
            if abs(size - target_bytes) / max(1, target_bytes) <= tol:
                return
            if size > target_bytes:
                if q > 25:
                    q = max(25, int(round(q * target_bytes / size)))
                else:
                    # 质量已到底仍超目标 → 降低分辨率继续逼近
                    scale *= math.sqrt(max(0.02, target_bytes / max(1, size)))
                    img = rgb0.resize(
                        (max(1, int(round(rgb0.width * scale))),
                         max(1, int(round(rgb0.height * scale)))),
                        Image.Resampling.LANCZOS)
            else:
                if q >= 98:
                    return  # 已到最高质量仍小于目标（受 WebP 尺寸上限等限制），无需再试
                q = min(98, int(round(q * target_bytes / size)))
        return
    # PNG：无损压缩，超目标则降分辨率（内容简单时小于目标属正常）
    img = composite
    scale = 1.0
    for _ in range(7):
        img.save(out_path, "PNG", compress_level=6)
        size = out_path.stat().st_size
        if size <= target_bytes * (1 + tol) or scale <= 0.06:
            return
        scale *= math.sqrt(max(0.02, target_bytes / max(1, size)))
        img = composite.resize(
            (max(1, int(round(composite.width * scale))),
             max(1, int(round(composite.height * scale)))),
            Image.Resampling.LANCZOS)


def theme_widget_tree(widget, t):
    """Best-effort recolor for tk widgets so theme switching does not leave white patches."""
    try:
        cls = widget.winfo_class()
    except Exception:
        return
    try:
        if cls in ('Frame', 'Toplevel', 'Labelframe'):
            widget.configure(bg=t['bg'] if cls == 'Toplevel' else t['panel'])
        elif cls == 'Label':
            widget.configure(bg=t['panel'], fg=t['fg'])
        elif cls == 'Checkbutton':
            widget.configure(bg=t['panel'], fg=t['fg'], activebackground=t['panel'], activeforeground=t['fg'], selectcolor=t['panel2'])
        elif cls == 'Radiobutton':
            widget.configure(bg=t['panel'], fg=t['fg'], activebackground=t['panel'], activeforeground=t['fg'], selectcolor=t['panel2'])
        elif cls == 'Canvas':
            pass
        elif cls == 'Scale':
            widget.configure(bg=t['panel'], fg=t['fg'], troughcolor=t['panel2'], highlightthickness=0)
    except Exception:
        pass
    for child in getattr(widget, 'winfo_children', lambda: [])():
        theme_widget_tree(child, t)


# ────────────────────────────────────────────────── data models ──────────────

@dataclass
class AppSettings:
    dark_mode: bool = False
    quick_export_path: str = ""


@dataclass
class ImageItem:
    path: Path


@dataclass
class Tile:
    source_idx: int
    weight: float
    crop_left: float = 0.0
    crop_right: float = 0.0
    crop_top: float = 0.0
    crop_bottom: float = 0.0

    def copy(self) -> "Tile":
        return Tile(
            self.source_idx,
            self.weight,
            self.crop_left,
            self.crop_right,
            self.crop_top,
            self.crop_bottom,
        )


class History:
    def __init__(self):
        self._stack: List[tuple] = []

    def push(self, tiles: List[Tile], zoom: float,
             active_tool: Optional[dict], focus_idx: Optional[int],
             inner_seams: Optional[Dict[int, dict]] = None):
        self._stack.append((
            [t.copy() for t in tiles], zoom,
            dict(active_tool) if active_tool else None,
            focus_idx,
            {int(k): dict(v) for k, v in (inner_seams or {}).items()},
        ))
        if len(self._stack) > 120:
            self._stack.pop(0)

    def pop(self) -> Optional[tuple]:
        return self._stack.pop() if self._stack else None


# ────────────────────────────────────────────────── Settings dialog ──────────

class Switch(tk.Canvas):
    """微信/飞书式开关：圆角滑块，点击切换。"""

    def __init__(self, master, variable, on_color="#00b578", off_color="#c8ccd4",
                 knob_color="#ffffff", outline_color="#aeb4bf", bg="#ffffff", command=None):
        super().__init__(master, width=ui(46), height=ui(26), bg=bg,
                         highlightthickness=0, bd=0)
        self._var = variable
        self._on_color = on_color
        self._off_color = off_color
        self._knob = knob_color
        self._outline = outline_color
        self._bg = bg
        self._cmd = command
        self.bind("<Button-1>", self._toggle)
        self._draw()

    def _toggle(self, _e=None):
        self._var.set(not bool(self._var.get()))
        self._draw()
        if self._cmd:
            self._cmd()

    def _draw(self):
        self.delete("all")
        on = bool(self._var.get())
        w, h = ui(46), ui(26)
        scale = 4
        W, H = w * scale, h * scale
        img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        fill = self._on_color if on else self._off_color
        # 药丸（4x 超采样，边缘平滑）
        d.rounded_rectangle((scale, scale, W - scale, H - scale),
                            radius=(H // 2 - scale), fill=fill)
        # 圆钮：白色带浅灰描边，左右内缩 2px，垂直居中（按真实像素换算到超采样空间）
        knob = (h - 8) * scale
        y0 = (H - knob) // 2
        x0 = (scale * 2) if not on else (W - knob - scale * 2)
        d.ellipse((x0, y0, x0 + knob, y0 + knob), fill=self._knob,
                  outline=self._outline, width=int(scale))
        img = img.resize((w, h), Image.Resampling.LANCZOS)
        self._photo = ImageTk.PhotoImage(img)
        self.create_image(0, 0, image=self._photo, anchor="nw")


class SettingsDialog(tk.Toplevel):
    def __init__(self, master, settings: AppSettings, on_save):
        super().__init__(master)
        self._app = master
        self.settings = settings
        self.on_save = on_save
        self.title("设置")
        self.geometry(f"{ui(560)}x{ui(400)}")
        self.minsize(ui(520), ui(360))
        self.resizable(False, False)
        t = master.theme()
        self.configure(bg=t["bg"])
        self.dark_var = tk.BooleanVar(value=settings.dark_mode)
        self.quick_path = settings.quick_export_path
        self._build(t)
        self.transient(master)
        self.grab_set()
        self.bind("<Escape>", lambda _: self._close())
        self.protocol("WM_DELETE_WINDOW", self._close)
        self._center()

    def _build(self, t):
        outer = tk.Frame(self, bg=t["bg"], padx=ui(14), pady=ui(14))
        outer.pack(fill="both", expand=True)
        body = tk.Frame(outer, bg=t["panel"], highlightthickness=1,
                        highlightbackground=t["border"])
        body.pack(fill="both", expand=True)

        # 左侧导航（微信式：灰底 + 高亮选中项）
        nav = tk.Frame(body, bg=t["panel2"], width=ui(150))
        nav.pack(side="left", fill="y")
        nav.pack_propagate(False)
        self._nav_lbls = {}
        for key, text in (("general", "通用"), ("about", "关于")):
            lbl = tk.Label(nav, text=text, bg=t["panel2"], fg=t["fg"],
                           font="{Microsoft YaHei} 11", padx=ui(18), pady=ui(11),
                           cursor="hand2", anchor="w")
            lbl.pack(fill="x", pady=(ui(14) if not self._nav_lbls else 0, 0))
            lbl.bind("<Button-1>", lambda _e, k=key: self._switch_nav(k))
            self._nav_lbls[key] = lbl

        # 右侧内容区
        self._content = tk.Frame(body, bg=t["panel"])
        self._content.pack(side="left", fill="both", expand=True)
        self._current_page = None
        self._switch_nav("general")

    def _switch_nav(self, key: str):
        t = self._app.theme()
        for k, lbl in self._nav_lbls.items():
            if k == key:
                lbl.configure(bg=t["ok"], fg="white", font="{Microsoft YaHei} 11 bold")
            else:
                lbl.configure(bg=t["panel2"], fg=t["fg"], font="{Microsoft YaHei} 11")
        for c in self._content.winfo_children():
            c.destroy()
        if key == "about":
            self._build_about(t)
        else:
            self._build_general(t)

    def _build_general(self, t):
        page = tk.Frame(self._content, bg=t["panel"], padx=ui(24), pady=ui(18))
        page.pack(fill="both", expand=True)
        tk.Label(page, text="通用", bg=t["panel"], fg=t["fg"],
                 font="{Microsoft YaHei} 14 bold").pack(anchor="w", pady=(0, ui(10)))

        def _row(title, desc):
            f = tk.Frame(page, bg=t["panel"])
            f.pack(fill="x", pady=ui(8))
            col = tk.Frame(f, bg=t["panel"])
            col.pack(side="left", fill="x", expand=True)
            tk.Label(col, text=title, bg=t["panel"], fg=t["fg"],
                     font="{Microsoft YaHei} 11").pack(anchor="w")
            tk.Label(col, text=desc, bg=t["panel"], fg=t["muted"],
                     font="{Microsoft YaHei} 9").pack(anchor="w", pady=(ui(3), 0))
            ctrl = tk.Frame(f, bg=t["panel"])
            ctrl.pack(side="right", padx=(ui(12), 0))
            return ctrl

        # 深色模式
        ctrl = _row("深色模式", "切换到深色主题，即时生效")
        off_c = "#c8ccd4" if not self._app.settings.dark_mode else "#525b66"
        Switch(ctrl, self.dark_var, on_color=t["ok"], off_color=off_c,
               knob_color="white", outline_color="#aeb4bf", bg=t["panel"]).pack(side="right")
        tk.Frame(page, bg=t["border"], height=1).pack(fill="x", pady=ui(10))

        # 导出路径
        ctrl2 = _row("导出路径", "设置后，快速导出与导出将直接保存到该目录")
        ttk.Button(ctrl2, text="选择文件夹", command=self._choose_quick_path,
                   style="Ghost.TButton").pack(side="right")
        self._path_lbl = tk.Label(page, text=self._fmt_path(self.quick_path),
                                  bg=t["panel"], fg=t["muted"], anchor="w",
                                  font="{Microsoft YaHei} 9")
        self._path_lbl.pack(fill="x", pady=(ui(2), 0))
        tk.Frame(page, bg=t["border"], height=1).pack(fill="x", pady=ui(10))

        # 完成
        btn_row = tk.Frame(page, bg=t["panel"])
        btn_row.pack(fill="x", pady=(ui(12), 0))
        ttk.Button(btn_row, text="完成", command=self._close, style="Accent.TButton").pack(side="right")

    def _build_about(self, t):
        page = tk.Frame(self._content, bg=t["panel"], padx=ui(24), pady=ui(20))
        page.pack(fill="both", expand=True)
        tk.Label(page, text="关于", bg=t["panel"], fg=t["fg"],
                 font="{Microsoft YaHei} 14 bold").pack(anchor="w", pady=(0, ui(12)))
        tk.Label(page, text="PicaCrop 图片拼接", bg=t["panel"], fg=t["fg"],
                 font="{Microsoft YaHei} 16 bold").pack(anchor="w")
        tk.Label(page, text="版本 1.0", bg=t["panel"], fg=t["muted"],
                 font="{Microsoft YaHei} 10").pack(anchor="w", pady=(ui(8), 0))
        tk.Label(page, text="多图拼接、自动对齐重叠截图、按目标体积导出。",
                 bg=t["panel"], fg=t["muted"], anchor="w", justify="left", wraplength=ui(280),
                 font="{Microsoft YaHei} 10").pack(anchor="w", pady=(ui(14), 0))

    def _fmt_path(self, p: str) -> str:
        return p if p else "未设置"

    def _choose_quick_path(self):
        d = filedialog.askdirectory(title="选择导出文件夹")
        if d:
            self.quick_path = d
            self._path_lbl.configure(text=self._fmt_path(d))

    def _center(self):
        self.update_idletasks()
        x = self._app.winfo_rootx() + (self._app.winfo_width() - self.winfo_width()) // 2
        y = self._app.winfo_rooty() + (self._app.winfo_height() - self.winfo_height()) // 2
        self.geometry(f"+{max(0, x)}+{max(0, y)}")

    def _close(self):
        self.settings.dark_mode = self.dark_var.get()
        self.settings.quick_export_path = self.quick_path
        self.on_save()
        self.destroy()


# ────────────────────────────────────────────────── Export dialog ────────────

class ExportDialog(tk.Toplevel):
    PRESETS = {
        "low": {"label": "低", "target_bytes": 1_000_000, "jpeg_hint": 72},
        "medium": {"label": "中", "target_bytes": 5_000_000, "jpeg_hint": 84},
        "high": {"label": "高", "target_bytes": 10_000_000, "jpeg_hint": 92},
    }

    def __init__(self, master, on_export):
        super().__init__(master)
        self._app = master
        self.on_export = on_export
        self.title("导出")
        self.geometry(f"{ui(520)}x{ui(420)}")
        self.minsize(ui(500), ui(340))
        self.resizable(False, False)
        t = master.theme()
        self.configure(bg=t["bg"])
        self.format_var = tk.StringVar(value="png")
        self.quality_var = tk.StringVar(value="high")
        self._build(t)
        self.transient(master)
        self.grab_set()
        self.bind("<Escape>", lambda _: self.destroy())
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self._center()

    def _build(self, t):
        outer = tk.Frame(self, bg=t["bg"], padx=ui(20), pady=ui(20))
        outer.pack(fill="both", expand=True)

        card = tk.Frame(
            outer, bg=t["panel"], highlightthickness=1,
            highlightbackground=t["border"], padx=ui(22), pady=ui(18)
        )
        card.pack(fill="both", expand=True)

        tk.Label(card, text="导出", bg=t["panel"], fg=t["fg"],
                 font="{Microsoft YaHei} 14 bold").pack(anchor="w", pady=(0, ui(12)))

        # 文件类型分组
        fmt_group = tk.Frame(card, bg=t["panel2"], highlightthickness=1,
                             highlightbackground=t["border"], padx=ui(14), pady=ui(12))
        fmt_group.pack(fill="x", pady=(0, ui(12)))
        tk.Label(fmt_group, text="文件类型", bg=t["panel2"], fg=t["fg"],
                 font="{Microsoft YaHei} 10 bold").pack(anchor="w", pady=(0, ui(8)))
        fmt_row = tk.Frame(fmt_group, bg=t["panel2"])
        fmt_row.pack(anchor="w")
        for lbl, val in (("PNG", "png"), ("JPG", "jpeg"), ("WebP", "webp"), ("PDF", "pdf")):
            ttk.Radiobutton(
                fmt_row, text=lbl, variable=self.format_var, value=val,
                style="App.TRadiobutton", command=self._toggle_format
            ).pack(side="left", padx=(0, ui(18)))

        # 体积分组
        vol_group = tk.Frame(card, bg=t["panel2"], highlightthickness=1,
                             highlightbackground=t["border"], padx=ui(14), pady=ui(12))
        vol_group.pack(fill="x")
        tk.Label(vol_group, text="体积", bg=t["panel2"], fg=t["fg"],
                 font="{Microsoft YaHei} 10 bold").pack(anchor="w", pady=(0, ui(8)))
        vol_row = tk.Frame(vol_group, bg=t["panel2"])
        vol_row.pack(anchor="w")
        self._quality_buttons = []
        for preset_key in ("low", "medium", "high"):
            rb = ttk.Radiobutton(
                vol_row, text=self.PRESETS[preset_key]["label"],
                variable=self.quality_var, value=preset_key,
                style="App.TRadiobutton", command=self._update_estimate
            )
            rb.pack(side="left", padx=(0, ui(18)))
            self._quality_buttons.append(rb)
        self._est_lbl = tk.Label(vol_group, text="", bg=t["panel2"], fg=t["blue"],
                                 font="{Microsoft YaHei} 10 bold", anchor="w")
        self._est_lbl.pack(anchor="w", pady=(ui(8), 0), fill="x")
        tk.Label(vol_group, text="确定后弹出保存位置",
                 bg=t["panel2"], fg=t["muted"],
                 font="{Microsoft YaHei} 9").pack(anchor="w", pady=(ui(2), 0))

        btn_row = tk.Frame(card, bg=t["panel"])
        btn_row.pack(anchor="e", side="bottom", fill="x", pady=(ui(22), 0))
        ttk.Button(btn_row, text="取消", command=self.destroy).pack(side="right")
        ttk.Button(btn_row, text="确认", command=self._submit, style="Accent.TButton").pack(side="right", padx=(0, ui(8)))
        self._toggle_format()

    def _toggle_format(self):
        self._update_estimate()

    def _center(self):
        self.update_idletasks()
        x = self._app.winfo_rootx() + (self._app.winfo_width() - self.winfo_width()) // 2
        y = self._app.winfo_rooty() + (self._app.winfo_height() - self.winfo_height()) // 2
        self.geometry(f"+{max(0, x)}+{max(0, y)}")

    def _fmt_size(self, n: int) -> str:
        if n <= 0:
            return "未知"
        if n < 1024 * 1024:
            return f"{max(1, round(n / 1024))} KB"
        return f"{n / (1024 * 1024):.1f} MB"

    def _update_estimate(self):
        key = self.quality_var.get()
        preset = self.PRESETS[key]
        self._est_lbl.configure(
            text=f"目标体积：约 {self._fmt_size(preset['target_bytes'])}")

    def _submit(self):
        fmt = self.format_var.get()
        preset = self.PRESETS[self.quality_var.get()]
        cfg = {
            "format": fmt,
            "target_bytes": preset["target_bytes"],
            "jpeg_hint": preset["jpeg_hint"],
        }
        self.on_export(cfg)
        self.destroy()


# ────────────────────────────────────────────────── Selection grid ───────────

class SelectionGrid(tk.Frame):
    def __init__(self, master, start_cb, settings_cb):
        super().__init__(master, bg=master.theme()["bg"])
        self._app = master
        self.start_cb = start_cb
        self.settings_cb = settings_cb
        self.work_dir: Optional[Path] = None
        self.items: List[ImageItem] = []
        self._thumb_refs: List[ImageTk.PhotoImage] = []
        self._thumb_cache: Dict[Path, ImageTk.PhotoImage] = {}
        self._thumb_refresh_job: Optional[str] = None
        self._load_token = 0
        self._loading = False
        self.selected: List[int] = []
        self._last_clicked: Optional[int] = None
        self._cells: List[tk.Canvas] = []
        self._path_index: Dict[Path, int] = {}
        self._hover_idx: Optional[int] = None
        self._badge_cache: Dict[tuple, ImageTk.PhotoImage] = {}
        self._ring_cache: Dict[tuple, ImageTk.PhotoImage] = {}
        self._build()

    def theme(self):
        return self._app.theme()

    def _build(self):
        t = self.theme()
        # 顶栏：应用名 + 副标题 + 设置（简洁，不做大卡片）
        self._header = tk.Frame(self, bg=t["bg"], padx=ui(24), pady=ui(14))
        self._header.pack(fill="x")
        title_row = tk.Frame(self._header, bg=t["bg"])
        title_row.pack(fill="x")
        self._hero_title = tk.Label(title_row, text="PicaCrop", bg=t["bg"], fg=t["fg"],
                                    font="{Microsoft YaHei} 20 bold")
        self._hero_title.pack(side="left")
        ttk.Button(title_row, text="设置", command=self.settings_cb, style="Ghost.TButton").pack(side="right")
        self._hero_sub = tk.Label(self._header, text="图片拼接 · 自动对齐重叠截图", bg=t["bg"], fg=t["muted"],
                                  font="{Microsoft YaHei} 10")
        self._hero_sub.pack(anchor="w", pady=(ui(2), 0))

        # 工具栏：主按钮「选择目录」+ 当前路径（垂直居中）
        self._toolbar = tk.Frame(self, bg=t["bg"], padx=ui(24), pady=ui(10))
        self._toolbar.pack(fill="x")
        ttk.Button(self._toolbar, text="选择目录", command=self._choose_dir, style="Accent.TButton").pack(side="left")
        self._path_lbl = tk.Label(self._toolbar, text="未选择目录", bg=t["bg"], fg=t["muted"],
                                  font="{Microsoft YaHei} 10")
        self._path_lbl.pack(side="left", padx=(ui(12), 0), pady=(ui(5), 0))

        # 内容区：白底卡片承载网格
        self._body_wrap = tk.Frame(self, bg=t["bg"], padx=ui(24), pady=0)
        self._body_wrap.pack(fill="both", expand=True)
        self._grid_card = tk.Frame(self._body_wrap, bg=t["panel"], highlightthickness=1,
                                   highlightbackground=t["border"])
        self._grid_card.pack(fill="both", expand=True)
        self._gc = tk.Canvas(self._grid_card, bg=t["panel"], highlightthickness=0, bd=0)
        self._vscroll = ttk.Scrollbar(self._grid_card, orient="vertical", command=self._gc.yview, style='App.Vertical.TScrollbar')
        self._inner = tk.Frame(self._gc, bg=t["panel"])
        self._gc.configure(yscrollcommand=self._vscroll.set)
        self._vscroll.pack(side="right", fill="y")
        self._gc.pack(side="left", fill="both", expand=True)
        self._win = self._gc.create_window((0, 0), window=self._inner, anchor="nw")
        self._inner.bind("<Configure>",
                          lambda e: self._gc.configure(scrollregion=self._gc.bbox("all")))
        self._gc.bind("<Configure>", self._on_grid_configure)
        self._gc.bind_all("<MouseWheel>", self._on_wheel)
        self.bind_all("<Escape>", lambda _e: self._clear() if self.winfo_ismapped() else None, add="+")

        # 底部操作栏：左取消，右拼接按钮 + 已选数
        self._bot_wrap = tk.Frame(self, bg=t["bg"], padx=ui(24), pady=ui(14))
        self._bot_wrap.pack(fill="x", side="bottom")
        self._bot_card = tk.Frame(self._bot_wrap, bg=t["panel"], highlightthickness=1,
                                  highlightbackground=t["border"], padx=ui(16), pady=ui(10))
        self._bot_card.pack(fill="x")
        ttk.Button(self._bot_card, text="取消选择（Esc）", command=self._clear, style="Ghost.TButton").pack(side="left")
        self._sel_lbl = tk.Label(self._bot_card, text="", bg=t["panel"], fg=t["blue"],
                                 font="{Microsoft YaHei} 10 bold")
        self._sel_lbl.pack(side="right", padx=(0, ui(8)))
        ttk.Button(self._bot_card, text="横向拼接", command=lambda: self._start("horizontal"),
                   style="Accent.TButton").pack(side="right", padx=(0, ui(8)))
        ttk.Button(self._bot_card, text="竖向拼接", command=lambda: self._start("vertical"),
                   style="Accent.TButton").pack(side="right", padx=(0, ui(8)))

    def apply_theme(self):
        t = self.theme()
        self.configure(bg=t["bg"])
        for w in (self._header, self._toolbar, self._body_wrap, self._bot_wrap):
            w.configure(bg=t["bg"])
        for w in (self._grid_card, self._bot_card):
            w.configure(bg=t["panel"], highlightbackground=t["border"])
        self._hero_title.configure(bg=t["bg"], fg=t["fg"])
        self._hero_sub.configure(bg=t["bg"], fg=t["muted"])
        self._gc.configure(bg=t["panel"])
        self._inner.configure(bg=t["panel"])
        self._path_lbl.configure(bg=t["bg"], fg=t["muted"])
        self._sel_lbl.configure(bg=t["panel"], fg=t["blue"])
        theme_widget_tree(self._grid_card, t)
        theme_widget_tree(self._bot_card, t)
        self._render_grid()

    def _on_wheel(self, event):
        if self.winfo_ismapped():
            self._gc.yview_scroll(-1 * int(event.delta / 120), "units")

    def _clear(self):
        self.selected.clear()
        self._last_clicked = None
        self._render_grid()

    def _choose_dir(self):
        d = filedialog.askdirectory()
        if not d:
            return
        self._load_dir(Path(d))

    def _load_dir(self, d: Path):
        self.work_dir = d
        self._load_token += 1
        token = self._load_token
        self._loading = True
        self.items = []
        self._thumb_cache.clear()
        self.selected.clear()
        self._last_clicked = None
        self._path_lbl.configure(text=f"正在扫描：{d}")
        self._render_grid()

        def task():
            try:
                paths = [p for p in sorted(d.iterdir()) if p.suffix.lower() in IMAGE_EXTS]
            except Exception:
                paths = []
            self.after(0, lambda: self._on_dir_scanned(token, paths))

        threading.Thread(target=task, daemon=True).start()

    def _on_dir_scanned(self, token: int, paths: List[Path]):
        if token != self._load_token:
            return
        self.items = [ImageItem(p) for p in paths]
        self._loading = False
        self._path_lbl.configure(text=f"{self.work_dir}    共 {len(self.items)} 张，ESC 可清空选择")
        self._render_grid()
        self._start_thumb_loader(token, paths)

    def _start_thumb_loader(self, token: int, paths: List[Path]):
        tw, th = self._thumb_size()

        def task():
            try:
                p2 = self.theme()["panel2"]
                p2_rgb = tuple(int(p2[i:i + 2], 16) for i in (1, 3, 5))
            except Exception:
                p2_rgb = (245, 248, 253)
            for path in paths:
                if token != self._load_token:
                    return
                try:
                    with Image.open(path) as _opened:
                        try:
                            _opened.draft("RGB", (tw * 4, th * 4))  # JPEG 只解码缩略所需像素，省内存
                        except Exception:
                            pass
                        im = ImageOps.exif_transpose(_opened.convert("RGB"))
                    # 方形满铺：无圆角无内边距，图片紧贴消除空隙
                    im = ImageOps.fit(im, (tw, th), Image.Resampling.LANCZOS)
                    card = im.convert("RGB")
                    # 文件名半透明条叠加到图片底部（照片墙风格，消除独立白条造成的空隙）
                    try:
                        bar_h = ui(22)
                        s2 = 2
                        strip = Image.new("RGBA", (tw * s2, bar_h * s2), (0, 0, 0, 0))
                        sd = ImageDraw.Draw(strip)
                        sd.rectangle((0, 0, tw * s2, bar_h * s2), fill=(0, 0, 0, 120))
                        try:
                            fn = ImageFont.truetype("msyh.ttc", 11 * s2)
                        except Exception:
                            try:
                                fn = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 11 * s2)
                            except Exception:
                                fn = ImageFont.load_default()
                        fname = path.name
                        maxw = (tw - ui(12)) * s2
                        while fname and sd.textlength(fname + "…", font=fn) > maxw:
                            fname = fname[:-1]
                        if fname != path.name:
                            fname += "…"
                        sd.text((ui(6) * s2, (bar_h * s2 - 13 * s2) // 2 + 2),
                                fname, font=fn, fill=(255, 255, 255, 255))
                        strip = strip.resize((tw, bar_h), Image.Resampling.LANCZOS)
                        overlay = Image.new("RGBA", (tw, th), (0, 0, 0, 0))
                        overlay.paste(strip, (0, th - bar_h))
                        card = Image.alpha_composite(card.convert("RGBA"), overlay).convert("RGB")
                    except Exception:
                        pass
                    self.after(0, lambda p=path, b=card.copy(), tkid=token: self._store_thumb(tkid, p, b))
                except Exception:
                    continue
            self.after(0, lambda tkid=token: self._thumbs_done(tkid))

        threading.Thread(target=task, daemon=True).start()

    def _store_thumb(self, token: int, path: Path, pil_img: Image.Image):
        if token != self._load_token:
            return
        self._thumb_cache[path] = ImageTk.PhotoImage(pil_img)
        idx = self._path_index.get(path)
        if idx is not None:
            self._apply_thumb(idx)

    def _apply_thumb(self, idx: int):
        if 0 <= idx < len(self._cells):
            self._paint_cell(idx)

    def _refresh_thumbs(self):
        # 已改为增量更新：单张缩略图就绪时只重绘该格，避免整表重建卡顿
        pass

    def _thumbs_done(self, token: int):
        if token != self._load_token:
            return
        self._path_lbl.configure(text=f"{self.work_dir}    共 {len(self.items)} 张，ESC 可清空选择")

    def _start(self, orientation: str):
        if len(self.selected) < 1:
            messagebox.showwarning("提示", "请先选择至少一张图片")
            return
        self.start_cb(self.work_dir, [self.items[i] for i in self.selected], orientation)

    def _thumb(self, path: Path) -> Optional[ImageTk.PhotoImage]:
        return self._thumb_cache.get(path)

    def _thumb_size(self) -> Tuple[int, int]:
        return (ui(180), ui(180))

    def _empty_icon(self, t) -> Optional[ImageTk.PhotoImage]:
        """空目录时绘制一个简洁的图片占位图标。"""
        try:
            from PIL import Image as _PImg, ImageDraw as _PDraw, ImageTk as _PTk
            s = 96
            img = _PImg.new("RGBA", (s, s), (0, 0, 0, 0))
            d = _PDraw.Draw(img)
            d.rounded_rectangle((3, 3, s - 3, s - 3), radius=ui(18),
                                fill=t["panel2"], outline=t["border"], width=3)
            d.ellipse((s // 2 - 11, s // 2 - 16, s // 2 + 11, s // 2 + 6), fill=t["blue_soft"])
            d.polygon([(18, s - 24), (s // 2, s - 48), (s - 18, s - 24)], fill=t["blue"])
            return _PTk.PhotoImage(img)
        except Exception:
            return None

    def _on_grid_configure(self, event):
        """窗口宽度变化时按需重排缩略图列数（防抖，避免频繁整表重建）。"""
        try:
            self._gc.itemconfigure(self._win, width=event.width)
        except Exception:
            pass
        if not self.items:
            return
        cw = self._gc.winfo_width()
        cols = 5 if cw > ui(1200) else (3 if cw < ui(800) else 4)
        if cols == getattr(self, "_grid_cols", 0):
            return
        if self._thumb_refresh_job is not None:
            try:
                self.after_cancel(self._thumb_refresh_job)
            except Exception:
                pass
        self._thumb_refresh_job = self.after(150, self._render_grid)

    def _toggle(self, idx: int, event):
        ctrl = bool(event.state & 0x4)
        shift = bool(event.state & 0x1)
        if shift and self._last_clicked is not None:
            a, b = sorted((self._last_clicked, idx))
            rng = list(range(a, b + 1))
            self.selected = (list(dict.fromkeys(self.selected + rng))
                             if ctrl else rng)
        elif ctrl:
            if idx in self.selected:
                self.selected.remove(idx)
            else:
                self.selected.append(idx)
            self._last_clicked = idx
        else:
            # 普通点击：再次点击已选中的图片 = 取消选中
            if idx in self.selected:
                self.selected.remove(idx)
            else:
                self.selected.append(idx)
            self._last_clicked = idx
        # 只重绘受影响的格子（点击的 + 所有已选中以刷新序号），避免整表重建卡顿
        for i in range(len(self.items)):
            if i == idx or i in self.selected:
                self._paint_cell(i)
        n = len(self.selected)
        self._sel_lbl.configure(text=f"已选 {n} 张" if n else "")

    def _render_grid(self):
        for c in self._inner.winfo_children():
            c.destroy()
        self._thumb_refs.clear()
        self._cells = []
        self._path_index = {}
        self._hover_idx = None
        t = self.theme()
        cw = self._gc.winfo_width()
        cols = 5 if cw > ui(1200) else (3 if cw < ui(800) else 4)
        self._grid_cols = cols
        tw, th = self._thumb_size()
        if not self.items:
            ph = tk.Frame(self._inner, bg=t["panel"])
            ph.grid(row=0, column=0, padx=ui(30), pady=ui(44), sticky="nsew")
            self._inner.grid_columnconfigure(0, weight=1)
            self._inner.grid_rowconfigure(0, weight=1)
            if self._loading:
                tk.Label(ph, text="⏳", bg=t["panel"], fg=t["blue_soft"],
                         font="{Segoe UI Emoji} 40").pack()
                tk.Label(ph, text="正在加载图片…", bg=t["panel"], fg=t["muted"],
                         font="{Microsoft YaHei} 12").pack(pady=(ui(10), 0))
            else:
                icon = self._empty_icon(t)
                if icon is not None:
                    # 保持引用，避免 PhotoImage 被垃圾回收导致图标不显示
                    self._empty_icon_photo = icon
                    tk.Label(ph, image=icon, bg=t["panel"]).pack()
                tk.Label(ph, text="当前目录没有可用图片", bg=t["panel"], fg=t["fg"],
                         font="{Microsoft YaHei} 13 bold").pack(pady=(ui(12), ui(4)))
                tk.Label(ph, text="支持 JPG / PNG / BMP / WebP / TIFF", bg=t["panel"], fg=t["muted"],
                         font="{Microsoft YaHei} 10").pack()
                tk.Label(ph, text="换个文件夹，或把截图放进来再选一次", bg=t["panel"], fg=t["muted"],
                         font="{Microsoft YaHei} 10").pack(pady=(ui(3), 0))
            self._sel_lbl.configure(text="")
            return
        # 清除空目录占位时设置的拉伸列/行，避免残留导致第一格被拉出大空隙
        try:
            self._inner.grid_columnconfigure(0, weight=0)
            self._inner.grid_rowconfigure(0, weight=0)
        except Exception:
            pass
        for idx, item in enumerate(self.items):
            r, c = idx // cols, idx % cols
            cell = tk.Canvas(self._inner, width=tw, height=th,
                             bg=t["panel"], highlightthickness=1,
                             highlightbackground=t["border"], bd=0, cursor="hand2")
            cell.grid(row=r, column=c, padx=ui(1), pady=ui(1))
            self._cells.append(cell)
            self._path_index[item.path] = idx
            self._paint_cell(idx)
            cell.bind("<Button-1>", lambda e, i=idx: self._toggle(i, e))
            cell.bind("<Enter>", lambda e, i=idx: self._on_cell_enter(i))
            cell.bind("<Leave>", lambda e, i=idx: self._on_cell_leave(i))
        n = len(self.selected)
        self._sel_lbl.configure(text=f"已选 {n} 张" if n else "")

    def _make_badge(self, order: int) -> ImageTk.PhotoImage:
        """PIL 超采样渲染序号徽标（3x 缩小），抗锯齿无锯齿。"""
        t = self.theme()
        key = (order, t["blue"])
        ph = self._badge_cache.get(key)
        if ph is not None:
            return ph
        scale = 3
        r = ui(19)
        size = (r * 2 + ui(8)) * scale
        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.ellipse((ui(1) * scale, ui(1) * scale, size - ui(1) * scale, size - ui(1) * scale),
                  fill=t["blue"], outline="white", width=max(1, int(ui(2) * scale)))
        try:
            fnt = ImageFont.truetype("arialbd.ttf", int(13 * scale))
        except Exception:
            try:
                fnt = ImageFont.truetype("C:/Windows/Fonts/arialbd.ttf", int(13 * scale))
            except Exception:
                fnt = ImageFont.load_default()
        bbox = d.textbbox((0, 0), str(order), font=fnt)
        tw_t = bbox[2] - bbox[0]
        th_t = bbox[3] - bbox[1]
        d.text(((size - tw_t) // 2 - bbox[0], (size - th_t) // 2 - bbox[1]),
               str(order), font=fnt, fill="white")
        img = img.resize((size // scale, size // scale), Image.Resampling.LANCZOS)
        ph = ImageTk.PhotoImage(img)
        self._badge_cache[key] = ph
        return ph

    def _make_ring(self, tw: int, th: int) -> ImageTk.PhotoImage:
        """PIL 超采样渲染圆角选中描边（2x 缩小），抗锯齿。"""
        t = self.theme()
        key = (tw, th, t["blue"])
        ph = self._ring_cache.get(key)
        if ph is not None:
            return ph
        scale = 2
        w, h = tw * scale, th * scale
        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.rectangle((2 * scale, 2 * scale, w - 2 * scale, h - 2 * scale),
                    outline=t["blue"], width=max(1, int(ui(3) * scale)))
        img = img.resize((tw, th), Image.Resampling.LANCZOS)
        ph = ImageTk.PhotoImage(img)
        self._ring_cache[key] = ph
        return ph

    def _on_cell_enter(self, idx: int):
        self._hover_idx = idx
        if 0 <= idx < len(self._cells):
            self._cells[idx].configure(highlightbackground=self.theme()["blue_soft"])

    def _on_cell_leave(self, idx: int):
        self._hover_idx = None
        if 0 <= idx < len(self._cells):
            self._cells[idx].configure(highlightbackground=self.theme()["border"])

    def _paint_cell(self, idx: int):
        if not (0 <= idx < len(self.items)) or idx >= len(self._cells):
            return
        cell = self._cells[idx]
        cell.delete("all")
        item = self.items[idx]
        t = self.theme()
        tw, th = self._thumb_size()
        # 图片区（上）
        ph = self._thumb(item.path)
        if ph is not None:
            self._thumb_refs.append(ph)
            cell.create_image(0, 0, image=ph, anchor="nw")
        else:
            cell.create_rectangle(1, 1, tw - 1, th - 1,
                                  fill=t["panel2"], outline="")
            cell.create_text(tw // 2, th // 2 - 8,
                             text="加载中", fill=t["muted"], font="{Microsoft YaHei} 10 bold")
            cell.create_text(tw // 2, th // 2 + 14,
                             text=item.path.suffix.upper().replace('.', ''), fill=t["muted"],
                             font="{Microsoft YaHei} 9")
        selected = idx in self.selected
        # 选中态：抗锯齿圆角描边 + 序号徽标（PIL 渲染，无锯齿）
        if selected:
            ring = self._make_ring(tw, th)
            self._thumb_refs.append(ring)
            cell.create_image(0, 0, image=ring, anchor="nw")
            order = self.selected.index(idx) + 1
            badge = self._make_badge(order)
            self._thumb_refs.append(badge)
            cell.create_image(ui(2), ui(2), image=badge, anchor="nw")


# ────────────────────────────────────────────────── Stitcher editor ──────────


class DoodleDialog(tk.Toplevel):
    def __init__(self, master, pil_image: Image.Image, on_confirm):
        super().__init__(master)
        self._app = master
        self.on_confirm = on_confirm
        self.original = pil_image.copy()
        self.title("涂鸦")
        self.configure(bg=master.theme()["bg"])
        try:
            scr_w = self.winfo_screenwidth()
            scr_h = self.winfo_screenheight()
            w = min(ui(1180), max(ui(760), (scr_w - ui(80)) if scr_w else ui(1180)))
            h = min(ui(860), max(ui(620), (scr_h - ui(120)) if scr_h else ui(860)))
            self.minsize(min(ui(960), w), min(ui(760), h))
            self.geometry(f"{w}x{h}")
        except Exception:
            self.minsize(ui(960), ui(760))
            self.geometry(f"{ui(1180)}x{ui(860)}")
        self.transient(master.winfo_toplevel())
        self.grab_set()

        max_w, max_h = ui(980), ui(640)
        scale = min(max_w / max(1, self.original.width), max_h / max(1, self.original.height), 1.0)
        self.display_size = (
            max(1, int(round(self.original.width * scale))),
            max(1, int(round(self.original.height * scale))),
        )
        self.display_image = self.original.resize(self.display_size, Image.Resampling.LANCZOS)
        self.photo = ImageTk.PhotoImage(self.display_image)

        self.tool_var = tk.StringVar(value="rect")
        self.color_var = tk.StringVar(value="#ff3b30")
        self.stroke_var = tk.IntVar(value=4)
        self.commands: List[dict] = []
        self.preview_item = None
        self.start_pt: Optional[Tuple[int, int]] = None
        self._palette_buttons: List[tk.Button] = []

        self._build()
        self.bind("<Escape>", lambda _e: self.destroy())
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self._center()

    def _build(self):
        t = self._app.theme()
        outer = tk.Frame(self, bg=t["bg"], padx=ui(16), pady=ui(16))
        outer.pack(fill="both", expand=True)

        top = tk.Frame(outer, bg=t["panel"], highlightthickness=1, highlightbackground=t["border"], padx=ui(14), pady=ui(10))
        top.pack(fill="x")
        for text, value in [("矩形", "rect"), ("圆形", "oval"), ("线条", "line"), ("箭头", "arrow"), ("文字", "text")]:
            tk.Radiobutton(
                top, text=text, variable=self.tool_var, value=value,
                bg=t["panel"], fg=t["fg"], activebackground=t["panel"], activeforeground=t["fg"],
                selectcolor=t["panel2"], font="{Microsoft YaHei} 10"
            ).pack(side="left", padx=(0, ui(10)))
        ttk.Button(top, text="撤销", command=self._undo).pack(side="right")
        ttk.Button(top, text="确认", command=self._confirm, style="Accent.TButton").pack(side="right", padx=(0, ui(8)))
        ttk.Button(top, text="取消", command=self.destroy).pack(side="right", padx=(0, ui(8)))

        controls = tk.Frame(outer, bg=t["panel"], highlightthickness=1, highlightbackground=t["border"], padx=ui(14), pady=ui(10))
        controls.pack(fill="x", pady=(ui(8), 0))
        tk.Label(controls, text="颜色", bg=t["panel"], fg=t["fg"], font="{Microsoft YaHei} 10 bold").pack(side="left")
        for color in ["#ff3b30", "#ff9500", "#ffcc00", "#34c759", "#0a84ff", "#5856d6", "#af52de", "#ffffff", "#000000"]:
            btn = tk.Button(controls, bg=color, width=2, relief="flat", bd=1,
                            command=lambda c=color: self._set_color(c))
            btn.pack(side="left", padx=ui(4))
            self._palette_buttons.append(btn)
        ttk.Button(controls, text="自定义颜色", command=self._choose_color).pack(side="left", padx=(ui(8), ui(16)))
        self._color_preview = tk.Label(controls, text=self.color_var.get(), bg=t["panel2"], fg=t["fg"], width=12)
        self._color_preview.pack(side="left", padx=(0, ui(16)))
        tk.Label(controls, text="粗细", bg=t["panel"], fg=t["fg"], font="{Microsoft YaHei} 10 bold").pack(side="left")
        self._stroke_scale = tk.Scale(controls, from_=1, to=24, orient="horizontal", variable=self.stroke_var,
                                      bg=t["panel"], fg=t["fg"], troughcolor=t["panel2"], highlightthickness=0,
                                      length=ui(180))
        self._stroke_scale.pack(side="left", padx=(ui(8), ui(6)))
        self._stroke_value = tk.Label(controls, text=str(self.stroke_var.get()), bg=t["panel2"], fg=t["fg"], width=4)
        self._stroke_value.pack(side="left")
        self.stroke_var.trace_add("write", lambda *_args: self._stroke_value.configure(text=str(self.stroke_var.get())))
        self._refresh_palette()

        hint = tk.Label(
            outer,
            text="Shift：线条/箭头保持水平或垂直；矩形/圆形保持标准正方形或正圆；文字点击位置后输入。",
            bg=t["bg"], fg=t["muted"], font="{Microsoft YaHei} 9"
        )
        hint.pack(anchor="w", pady=(ui(8), ui(8)))

        card = tk.Frame(outer, bg=t["panel"], highlightthickness=1, highlightbackground=t["border"], padx=ui(14), pady=ui(12))
        card.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(card, bg=t["canvas"], highlightthickness=0, bd=0,
                                width=self.display_size[0], height=self.display_size[1])
        self.canvas.pack(expand=True)
        self.canvas.create_image(0, 0, image=self.photo, anchor="nw", tags=("bg",))
        self.canvas.bind("<Button-1>", self._press)
        self.canvas.bind("<B1-Motion>", self._drag)
        self.canvas.bind("<ButtonRelease-1>", self._release)

    def _center(self):
        self.update_idletasks()
        root = self._app.winfo_toplevel()
        x = root.winfo_rootx() + max(0, (root.winfo_width() - self.winfo_width()) // 2)
        y = root.winfo_rooty() + max(0, (root.winfo_height() - self.winfo_height()) // 2)
        self.geometry(f"+{x}+{y}")

    def _set_color(self, color: str):
        self.color_var.set(color)
        self._color_preview.configure(text=color, bg=color, fg=("white" if color.lower() not in ("#ffffff", "white") else "black"))
        self._refresh_palette()

    def _choose_color(self):
        _rgb, value = colorchooser.askcolor(color=self.color_var.get(), parent=self, title="选择颜色")
        if value:
            self._set_color(value)

    def _refresh_palette(self):
        current = self.color_var.get().lower()
        for btn in self._palette_buttons:
            try:
                btn.configure(highlightthickness=2 if str(btn["bg"]).lower() == current else 1,
                              highlightbackground=self._app.theme()["blue" if str(btn["bg"]).lower() == current else "border"])
            except Exception:
                pass

    def _current_width(self) -> int:
        return max(1, int(self.stroke_var.get()))

    def _preview_font(self, width: int) -> str:
        return f"{{Microsoft YaHei}} {max(12, width * 4)} bold"

    def _constrain(self, x0: int, y0: int, x1: int, y1: int, shift: bool) -> Tuple[int, int]:
        if not shift:
            return x1, y1
        tool = self.tool_var.get()
        dx, dy = x1 - x0, y1 - y0
        if tool in ("line", "arrow"):
            if abs(dx) >= abs(dy):
                return x1, y0
            return x0, y1
        if tool in ("rect", "oval"):
            size = max(abs(dx), abs(dy))
            return x0 + (size if dx >= 0 else -size), y0 + (size if dy >= 0 else -size)
        return x1, y1

    def _press(self, event):
        x, y = int(event.x), int(event.y)
        tool = self.tool_var.get()
        color = self.color_var.get()
        width = self._current_width()
        if tool == "text":
            text = simpledialog.askstring("输入文字", "请输入要添加的文字：", parent=self)
            if text:
                self.commands.append({"tool": "text", "x": x, "y": y, "text": text, "color": color, "width": width})
                self._redraw_commands()
            return
        self.start_pt = (x, y)
        self.preview_item = None

    def _drag(self, event):
        if self.start_pt is None:
            return
        x0, y0 = self.start_pt
        x1, y1 = self._constrain(x0, y0, int(event.x), int(event.y), bool(event.state & 0x1))
        width = self._current_width()
        color = self.color_var.get()
        if self.preview_item is not None:
            self.canvas.delete(self.preview_item)
        tool = self.tool_var.get()
        if tool == "rect":
            self.preview_item = self.canvas.create_rectangle(x0, y0, x1, y1, outline=color, width=width)
        elif tool == "oval":
            self.preview_item = self.canvas.create_oval(x0, y0, x1, y1, outline=color, width=width)
        elif tool == "line":
            self.preview_item = self.canvas.create_line(x0, y0, x1, y1, fill=color, width=width)
        elif tool == "arrow":
            self.preview_item = self.canvas.create_line(x0, y0, x1, y1, fill=color, width=width, arrow=tk.LAST)

    def _release(self, event):
        if self.start_pt is None:
            return
        x0, y0 = self.start_pt
        x1, y1 = self._constrain(x0, y0, int(event.x), int(event.y), bool(event.state & 0x1))
        color = self.color_var.get()
        width = self._current_width()
        self.start_pt = None
        if self.preview_item is not None:
            self.canvas.delete(self.preview_item)
            self.preview_item = None
        if abs(x1 - x0) < 2 and abs(y1 - y0) < 2:
            return
        self.commands.append({"tool": self.tool_var.get(), "x0": x0, "y0": y0, "x1": x1, "y1": y1, "color": color, "width": width})
        self._redraw_commands()

    def _redraw_commands(self):
        self.canvas.delete("drawcmd")
        for cmd in self.commands:
            tool = cmd["tool"]
            color = cmd.get("color", "#ff3b30")
            width = max(1, int(cmd.get("width", 4)))
            if tool == "rect":
                self.canvas.create_rectangle(cmd["x0"], cmd["y0"], cmd["x1"], cmd["y1"],
                                             outline=color, width=width, tags=("drawcmd",))
            elif tool == "oval":
                self.canvas.create_oval(cmd["x0"], cmd["y0"], cmd["x1"], cmd["y1"],
                                        outline=color, width=width, tags=("drawcmd",))
            elif tool == "line":
                self.canvas.create_line(cmd["x0"], cmd["y0"], cmd["x1"], cmd["y1"],
                                        fill=color, width=width, tags=("drawcmd",))
            elif tool == "arrow":
                self.canvas.create_line(cmd["x0"], cmd["y0"], cmd["x1"], cmd["y1"],
                                        fill=color, width=width, arrow=tk.LAST, tags=("drawcmd",))
            elif tool == "text":
                self.canvas.create_text(cmd["x"], cmd["y"], text=cmd["text"], fill=color,
                                        font=self._preview_font(width), anchor="nw", tags=("drawcmd",))

    def _undo(self):
        if self.commands:
            self.commands.pop()
            self._redraw_commands()

    def _draw_arrow(self, draw: ImageDraw.ImageDraw, x0: float, y0: float, x1: float, y1: float, color: str, width: int):
        draw.line((x0, y0, x1, y1), fill=color, width=width)
        angle = math.atan2(y1 - y0, x1 - x0)
        head_len = max(14, width * 4)
        head_a = angle + math.radians(150)
        head_b = angle - math.radians(150)
        ax = x1 + head_len * math.cos(head_a)
        ay = y1 + head_len * math.sin(head_a)
        bx = x1 + head_len * math.cos(head_b)
        by = y1 + head_len * math.sin(head_b)
        draw.line((x1, y1, ax, ay), fill=color, width=width)
        draw.line((x1, y1, bx, by), fill=color, width=width)

    def _load_text_font(self, size: int):
        candidates = [
            "C:/Windows/Fonts/msyh.ttc",
            "C:/Windows/Fonts/msyhbd.ttc",
            "C:/Windows/Fonts/simhei.ttf",
            "arial.ttf",
        ]
        for path in candidates:
            try:
                return ImageFont.truetype(path, size=size)
            except Exception:
                continue
        return ImageFont.load_default()

    def _confirm(self):
        result = self.original.copy()
        draw = ImageDraw.Draw(result)
        sx = self.original.width / max(1, self.display_size[0])
        sy = self.original.height / max(1, self.display_size[1])
        for cmd in self.commands:
            tool = cmd["tool"]
            color = cmd.get("color", "#ff3b30")
            width = max(1, int(round(cmd.get("width", 4) * max(sx, sy))))
            if tool in ("rect", "oval", "line", "arrow"):
                x0 = cmd["x0"] * sx
                y0 = cmd["y0"] * sy
                x1 = cmd["x1"] * sx
                y1 = cmd["y1"] * sy
                if tool == "rect":
                    draw.rectangle((x0, y0, x1, y1), outline=color, width=width)
                elif tool == "oval":
                    draw.ellipse((x0, y0, x1, y1), outline=color, width=width)
                elif tool == "line":
                    draw.line((x0, y0, x1, y1), fill=color, width=width)
                elif tool == "arrow":
                    self._draw_arrow(draw, x0, y0, x1, y1, color, width)
            elif tool == "text":
                font = self._load_text_font(max(14, int(round(cmd.get("width", 4) * 8 * max(sx, sy)))))
                draw.text((cmd["x"] * sx, cmd["y"] * sy), cmd["text"], fill=color, font=font)
        self.on_confirm(result)
        self.destroy()




class TransparencyControlWindow(tk.Toplevel):
    def __init__(self, preview_window: "FloatingPreviewWindow"):
        super().__init__(preview_window._app.winfo_toplevel())
        self.preview_window = preview_window
        self.title("置顶控制")
        self.geometry(f"{ui(320)}x{ui(170)}")
        self.minsize(ui(300), ui(150))
        self.resizable(False, False)
        self.attributes("-topmost", True)
        self.configure(bg=preview_window._app.theme()["panel"])
        self.protocol("WM_DELETE_WINDOW", self._close_and_preview)

        t = preview_window._app.theme()
        wrap = tk.Frame(self, bg=t["panel"], padx=ui(16), pady=ui(12))
        wrap.pack(fill="both", expand=True)

        self._label = tk.Label(
            wrap,
            text="透明度：100%",
            bg=t["panel"],
            fg=t["fg"],
            font="{Microsoft YaHei} 10 bold",
        )
        self._label.pack(anchor="w")

        self.scale = tk.Scale(
            wrap,
            from_=0,
            to=100,
            orient="horizontal",
            resolution=1,
            showvalue=False,
            command=self._on_scale,
            length=ui(280),
            bg=t["panel"],
            fg=t["fg"],
            troughcolor=t["panel2"],
            highlightthickness=0,
        )
        self.scale.pack(fill="x", pady=(ui(8), ui(6)))

        opts = tk.Frame(wrap, bg=t["panel"])
        opts.pack(fill="x", pady=(2, 0))
        tk.Checkbutton(
            opts,
            text="窗口置顶",
            variable=preview_window._topmost_var,
            command=preview_window._apply_topmost,
            bg=t["panel"], fg=t["fg"],
            activebackground=t["panel"], activeforeground=t["fg"],
            selectcolor=t["panel2"],
        ).pack(anchor="w")

        tip = tk.Label(
            wrap,
            text="关闭此控制窗口，会同步关闭置顶窗口。左右方向键每次调节 1%，支持调到 0%。",
            bg=t["panel"],
            fg=t["muted"],
            font="{Microsoft YaHei} 9",
            justify="left",
        )
        tip.pack(anchor="w", pady=(ui(8), 0))

        self.bind("<Left>", lambda e: self._step(-1))
        self.bind("<Right>", lambda e: self._step(1))
        self.bind("<Up>", lambda e: self._step(1))
        self.bind("<Down>", lambda e: self._step(-1))
        self.scale.bind("<Left>", lambda e: (self._step(-1), "break")[1])
        self.scale.bind("<Right>", lambda e: (self._step(1), "break")[1])
        self.scale.bind("<Up>", lambda e: (self._step(1), "break")[1])
        self.scale.bind("<Down>", lambda e: (self._step(-1), "break")[1])
        self.scale.focus_set()
        self.set_alpha_percent(int(round(preview_window._alpha * 100)))

    def apply_theme(self):
        t = self.preview_window._app.theme()
        self.configure(bg=t["panel"])
        theme_widget_tree(self, t)
        self._label.configure(bg=t["panel"], fg=t["fg"])

    def _step(self, delta: int):
        v = max(0, min(100, int(self.scale.get()) + int(delta)))
        self.scale.set(v)
        self._emit(v)

    def _on_scale(self, _value):
        self._emit(int(round(float(self.scale.get()))))

    def _emit(self, value: int):
        self._label.configure(text=f"透明度：{int(value)}%")
        self.preview_window._set_alpha(value / 100.0, from_control=True)

    def set_alpha_percent(self, value: int):
        value = max(0, min(100, int(value)))
        try:
            self.scale.set(value)
        except Exception:
            pass
        self._label.configure(text=f"透明度：{value}%")

    def _close_and_preview(self):
        try:
            self.preview_window._control_window = None
        except Exception:
            pass
        try:
            self.destroy()
        except Exception:
            pass
        try:
            self.preview_window._close(from_control=True)
        except Exception:
            pass


class FloatingPreviewWindow(tk.Toplevel):
    RESIZE_MARGIN = ui(10)

    def __init__(self, master, on_close, image_provider=None):
        super().__init__(master.winfo_toplevel())
        self._app = master
        self._on_close = on_close
        self._image_provider = image_provider
        self._source_image: Optional[Image.Image] = None
        self._photo: Optional[ImageTk.PhotoImage] = None
        self._redraw_job = None
        self._alpha = 0.96
        self._topmost_var = tk.BooleanVar(value=True)
        self._control_window: Optional[TransparencyControlWindow] = None
        self._drag_anchor: Optional[Tuple[int, int]] = None
        self._window_anchor: Optional[Tuple[int, int]] = None
        self._display_size: Tuple[int, int] = (0, 0)
        self._resizing = False
        self._resize_edges = ""
        self._resize_origin: Optional[Tuple[int, int, int, int, int, int]] = None
        self._natural_geometry_set = False
        self._closing = False
        self._aspect_ratio = 1.0
        self._native_old_proc = None
        self._native_proc = None
        self._base_exstyles = {}

        self.title("置顶页面")
        self.geometry(f"{ui(480)}x{ui(320)}")
        self.minsize(ui(120), ui(80))
        self.resizable(False, False)
        self.configure(bg=master.theme()["canvas"])
        self.overrideredirect(True)
        self.attributes("-alpha", self._alpha)
        self.attributes("-topmost", True)

        self.canvas = tk.Canvas(self, bg=master.theme()["canvas"], highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)
        self._image_item = self.canvas.create_image(0, 0, anchor="nw")
        self.canvas.bind("<ButtonPress-1>", self._pointer_press)
        self.canvas.bind("<B1-Motion>", self._pointer_drag)
        self.canvas.bind("<ButtonRelease-1>", self._pointer_release)
        self.canvas.bind("<Motion>", self._pointer_motion)
        self.bind("<Configure>", self._on_configure)
        self.bind("<Escape>", lambda _e: self._close())
        self.protocol("WM_DELETE_WINDOW", self._close)

        self.after(80, self._init_native_window)

    def _init_native_window(self):
        self._apply_native_window_style()
        self._apply_topmost()

    def _get_aspect_ratio(self) -> float:
        if self._aspect_ratio > 0:
            return self._aspect_ratio
        if self._source_image is not None and self._source_image.height:
            return max(0.01, self._source_image.width / self._source_image.height)
        w, h = self.winfo_width(), self.winfo_height()
        return max(0.01, w / max(1, h))

    def _normalized_size(self, width: int, height: int) -> Tuple[int, int]:
        width = max(1, int(width))
        height = max(1, int(height))
        screen_w = max(200, self.winfo_screenwidth() - 40)
        screen_h = max(120, self.winfo_screenheight() - 80)
        scale = min(screen_w / width, screen_h / height, 1.0)
        width = max(ui(80), int(round(width * scale)))
        height = max(ui(60), int(round(height * scale)))
        return width, height

    def _fit_aspect_size(self, *, target_w: Optional[int] = None, target_h: Optional[int] = None) -> Tuple[int, int]:
        ratio = self._get_aspect_ratio()
        screen_w = max(200, self.winfo_screenwidth() - 10)
        screen_h = max(120, self.winfo_screenheight() - 10)
        if target_w is None and target_h is None:
            target_w, target_h = self.winfo_width(), self.winfo_height()
        elif target_w is None:
            target_h = max(1, int(round(target_h)))
            target_w = max(1, int(round(target_h * ratio)))
        elif target_h is None:
            target_w = max(1, int(round(target_w)))
            target_h = max(1, int(round(target_w / ratio)))
        else:
            target_w = max(1, int(round(target_w)))
            target_h = max(1, int(round(target_h)))

        min_w = ui(80)
        min_h = ui(60)
        scale_down = min(screen_w / max(1, target_w), screen_h / max(1, target_h), 1.0)
        target_w = max(1, int(round(target_w * scale_down)))
        target_h = max(1, int(round(target_h * scale_down)))

        if target_w < min_w:
            target_w = min_w
            target_h = max(1, int(round(target_w / ratio)))
        if target_h < min_h:
            target_h = min_h
            target_w = max(1, int(round(target_h * ratio)))

        scale_down = min(screen_w / max(1, target_w), screen_h / max(1, target_h), 1.0)
        target_w = max(1, int(round(target_w * scale_down)))
        target_h = max(1, int(round(target_h * scale_down)))
        return int(target_w), int(target_h)

    def _install_native_hit_test_proc(self):
        # v10: 移除自定义 WNDPROC 钩子。上一版这里最容易导致置顶窗口点击后闪退。
        self._native_proc = None
        self._native_old_proc = None

    def _remove_native_hit_test_proc(self):
        self._native_proc = None
        self._native_old_proc = None

    def _iter_native_hwnds(self):
        hwnds = []
        try:
            top = int(self.winfo_id())
            if top:
                hwnds.append(top)
        except Exception:
            pass
        try:
            canvas_hwnd = int(self.canvas.winfo_id())
            if canvas_hwnd and canvas_hwnd not in hwnds:
                hwnds.append(canvas_hwnd)
        except Exception:
            pass
        if sys.platform.startswith("win") and ctypes is not None and hwnds:
            try:
                user32 = ctypes.windll.user32
                EnumChildWindows = user32.EnumChildWindows
                EnumChildWindows.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
                EnumChildWindows.restype = ctypes.c_bool
                CALLBACK = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
                seen = set(hwnds)
                def _cb(ch, _lp):
                    try:
                        ch = int(ch)
                    except Exception:
                        return True
                    if ch and ch not in seen:
                        seen.add(ch)
                        hwnds.append(ch)
                    return True
                self._enum_child_proc = CALLBACK(_cb)
                EnumChildWindows(int(self.winfo_id()), self._enum_child_proc, 0)
            except Exception:
                pass
        return hwnds

    def _apply_native_window_style(self):
        if not sys.platform.startswith("win") or ctypes is None:
            return
        try:
            self.update_idletasks()
        except Exception:
            pass
        try:
            GWL_EXSTYLE = -20
            SWP_NOSIZE = 0x0001
            SWP_NOMOVE = 0x0002
            SWP_NOZORDER = 0x0004
            SWP_FRAMECHANGED = 0x0020
            HWND_TOPMOST = -1
            HWND_NOTOPMOST = -2

            user32 = ctypes.windll.user32
            get_style = getattr(user32, "GetWindowLongPtrW", user32.GetWindowLongW)
            set_style = getattr(user32, "SetWindowLongPtrW", user32.SetWindowLongW)
            top_hwnd = int(self.winfo_id())
            hwnds = self._iter_native_hwnds()
            for hwnd in hwnds:
                current_style = int(get_style(hwnd, GWL_EXSTYLE))
                if hwnd not in self._base_exstyles:
                    self._base_exstyles[hwnd] = current_style
                base = int(self._base_exstyles[hwnd])
                set_style(hwnd, GWL_EXSTYLE, base)

            try:
                zorder = HWND_TOPMOST if bool(self._topmost_var.get()) else HWND_NOTOPMOST
                user32.SetWindowPos(
                    top_hwnd, zorder, 0, 0, 0, 0,
                    SWP_NOMOVE | SWP_NOSIZE | SWP_FRAMECHANGED
                )
            except Exception:
                try:
                    user32.SetWindowPos(
                        top_hwnd, 0, 0, 0, 0, 0,
                        SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_FRAMECHANGED
                    )
                except Exception:
                    pass
        except Exception:
            pass

    def apply_theme(self):
        try:
            self.configure(bg=self._app.theme()["canvas"])
            self.canvas.configure(bg=self._app.theme()["canvas"])
        except Exception:
            pass
        if self._control_window is not None and self._control_window.winfo_exists():
            try:
                self._control_window.apply_theme()
            except Exception:
                pass
        self._schedule_redraw()

    def present(self):
        try:
            self.deiconify()
            self._ensure_control_window()
            self._apply_topmost()
            self.lift()
        except Exception:
            pass

    def _ensure_control_window(self):
        if self._control_window is None or not self._control_window.winfo_exists():
            self._control_window = TransparencyControlWindow(self)
            try:
                x = min(self.winfo_screenwidth() - 340, max(20, self.winfo_x() + 24))
                y = min(self.winfo_screenheight() - 220, max(20, self.winfo_y() + 24))
                self._control_window.geometry(f"+{x}+{y}")
            except Exception:
                pass
        else:
            try:
                self._control_window.deiconify()
                self._control_window.lift()
            except Exception:
                pass
        try:
            self._control_window.set_alpha_percent(int(round(self._alpha * 100)))
        except Exception:
            pass

    def _pull_fresh_source(self) -> Optional[Image.Image]:
        if callable(self._image_provider):
            try:
                img = self._image_provider()
                if img is not None:
                    return img.copy()
            except Exception:
                return None
        return None

    def update_image(self, pil_image: Image.Image):
        self._source_image = pil_image.copy()
        if self._source_image.height:
            self._aspect_ratio = max(0.01, self._source_image.width / self._source_image.height)
        if not self._natural_geometry_set:
            dw, dh = self._normalized_size(self._source_image.width, self._source_image.height)
            try:
                x = max(20, (self.winfo_screenwidth() - dw) // 2)
                y = max(20, (self.winfo_screenheight() - dh) // 2)
                self.geometry(f"{dw}x{dh}+{x}+{y}")
                self._natural_geometry_set = True
            except Exception:
                pass
        else:
            try:
                nw, nh = self._fit_aspect_size(target_w=self.winfo_width())
                self.geometry(f"{nw}x{nh}+{self.winfo_x()}+{self.winfo_y()}")
            except Exception:
                pass
        self._schedule_redraw()

    def _schedule_redraw(self):
        if self._redraw_job is not None:
            self.after_cancel(self._redraw_job)
        self._redraw_job = self.after(20, self._redraw)

    def _client_size(self) -> Tuple[int, int]:
        return max(1, self.winfo_width()), max(1, self.winfo_height())

    def _redraw(self):
        self._redraw_job = None
        if self._source_image is None:
            fresh = self._pull_fresh_source()
            if fresh is not None:
                self._source_image = fresh
                if self._source_image.height:
                    self._aspect_ratio = max(0.01, self._source_image.width / self._source_image.height)
        if self._source_image is None:
            return
        img = self._source_image.copy()
        cw, ch = self._client_size()
        if img.width <= 0 or img.height <= 0:
            return
        if (cw, ch) != img.size:
            img = img.resize((cw, ch), Image.Resampling.LANCZOS)
        self._display_size = (cw, ch)
        self.canvas.configure(width=cw, height=ch, bg=self._app.theme()["canvas"])
        self.configure(bg=self._app.theme()["canvas"])
        self._photo = ImageTk.PhotoImage(img)
        self.canvas.itemconfigure(self._image_item, image=self._photo)
        self.canvas.coords(self._image_item, 0, 0)

    def _cursor_for_edges(self, edges: str) -> str:
        if edges in ("nw", "se"):
            return "size_nw_se"
        if edges in ("ne", "sw"):
            return "size_ne_sw"
        if edges in ("n", "s"):
            return "sb_v_double_arrow"
        if edges in ("e", "w"):
            return "sb_h_double_arrow"
        return "fleur"

    def _hit_resize_edges(self, x: int, y: int) -> str:
        w, h = self._client_size()
        m = self.RESIZE_MARGIN
        left = x <= m
        right = x >= w - m
        top = y <= m
        bottom = y >= h - m
        edges = ""
        if top:
            edges += "n"
        elif bottom:
            edges += "s"
        if left:
            edges += "w"
        elif right:
            edges += "e"
        return edges

    def _pointer_motion(self, event):
        if self._resizing:
            return
        edges = self._hit_resize_edges(event.x, event.y)
        cursor = self._cursor_for_edges(edges) if edges else "fleur"
        try:
            self.configure(cursor=cursor)
            self.canvas.configure(cursor=cursor)
        except Exception:
            pass

    def _pointer_press(self, event):
        edges = self._hit_resize_edges(event.x, event.y)
        if edges:
            self._resizing = True
            self._resize_edges = edges
            self._resize_origin = (event.x_root, event.y_root, self.winfo_x(), self.winfo_y(), self.winfo_width(), self.winfo_height())
            return
        self._drag_anchor = (event.x_root, event.y_root)
        self._window_anchor = (self.winfo_x(), self.winfo_y())

    def _aspect_resize_from_origin(self, dx: int, dy: int) -> Tuple[int, int, int, int]:
        if self._resize_origin is None:
            return self.winfo_x(), self.winfo_y(), self.winfo_width(), self.winfo_height()
        _sx, _sy, wx, wy, ww, wh = self._resize_origin
        edges = self._resize_edges

        target_w = ww
        target_h = wh
        if "e" in edges:
            target_w = ww + dx
        elif "w" in edges:
            target_w = ww - dx
        if "s" in edges:
            target_h = wh + dy
        elif "n" in edges:
            target_h = wh - dy

        if ("e" in edges or "w" in edges) and not ("n" in edges or "s" in edges):
            new_w, new_h = self._fit_aspect_size(target_w=target_w)
        elif ("n" in edges or "s" in edges) and not ("e" in edges or "w" in edges):
            new_w, new_h = self._fit_aspect_size(target_h=target_h)
        else:
            cand_w1, cand_h1 = self._fit_aspect_size(target_w=target_w)
            cand_w2, cand_h2 = self._fit_aspect_size(target_h=target_h)
            err1 = abs(cand_w1 - target_w) + abs(cand_h1 - target_h)
            err2 = abs(cand_w2 - target_w) + abs(cand_h2 - target_h)
            if err1 <= err2:
                new_w, new_h = cand_w1, cand_h1
            else:
                new_w, new_h = cand_w2, cand_h2

        new_x = wx + (ww - new_w) if "w" in edges else wx
        new_y = wy + (wh - new_h) if "n" in edges else wy

        screen_w = self.winfo_screenwidth()
        screen_h = self.winfo_screenheight()
        new_x = max(0, min(screen_w - new_w, int(new_x)))
        new_y = max(0, min(screen_h - new_h, int(new_y)))
        return int(new_x), int(new_y), int(new_w), int(new_h)

    def _pointer_drag(self, event):
        if self._resizing and self._resize_origin is not None:
            sx, sy, _wx, _wy, _ww, _wh = self._resize_origin
            dx = event.x_root - sx
            dy = event.y_root - sy
            new_x, new_y, new_w, new_h = self._aspect_resize_from_origin(dx, dy)
            self.geometry(f"{new_w}x{new_h}+{new_x}+{new_y}")
            return
        if self._drag_anchor is None or self._window_anchor is None:
            return
        dx = event.x_root - self._drag_anchor[0]
        dy = event.y_root - self._drag_anchor[1]
        self.geometry(f"+{self._window_anchor[0] + dx}+{self._window_anchor[1] + dy}")

    def _pointer_release(self, _event):
        self._drag_anchor = None
        self._window_anchor = None
        self._resizing = False
        self._resize_origin = None
        self._resize_edges = ""

    def _on_configure(self, _event=None):
        self._schedule_redraw()
        self.after(5, self._apply_native_window_style)

    def _set_alpha(self, value: float, from_control: bool = False):
        self._alpha = max(0.0, min(1.0, float(value)))
        try:
            self.attributes("-alpha", self._alpha)
        except Exception:
            pass
        self._apply_native_window_style()
        if not from_control and self._control_window is not None and self._control_window.winfo_exists():
            try:
                self._control_window.set_alpha_percent(int(round(self._alpha * 100)))
            except Exception:
                pass

    def _apply_topmost(self):
        enabled = bool(self._topmost_var.get())
        try:
            self.attributes("-topmost", enabled)
            self.after(30, lambda: self.attributes("-topmost", enabled))
            if enabled:
                self.lift()
        except Exception:
            pass
        self._apply_native_window_style()
        if self._control_window is not None and self._control_window.winfo_exists():
            try:
                self._control_window.attributes("-topmost", True)
                self._control_window.lift()
            except Exception:
                pass

    def _close(self, from_control: bool = False):
        if self._closing:
            return
        self._closing = True
        try:
            if self._control_window is not None and self._control_window.winfo_exists() and not from_control:
                self._control_window.destroy()
        except Exception:
            pass
        finally:
            self._control_window = None
        try:
            self._remove_native_hit_test_proc()
        except Exception:
            pass
        try:
            self._apply_native_window_style()
        except Exception:
            pass
        try:
            self.destroy()
        finally:
            self._on_close()


class StitcherEditor(tk.Frame):
    @staticmethod
    def _handle_r() -> int:
        return ui(22)

    @staticmethod
    def _sidebar_min() -> int:
        return ui(260)

    @staticmethod
    def _sidebar_max() -> int:
        return ui(520)

    def __init__(self, master, back_cb, settings_cb):
        super().__init__(master, bg=master.theme()["bg"])
        self._app = master
        self.back_cb = back_cb
        self.settings_cb = settings_cb

        self.work_dir: Optional[Path] = None
        self.items: List[ImageItem] = []
        self.orientation = "horizontal"
        self.zoom = 1.0
        self.tiles: List[Tile] = []
        self.preview_sources: List[Image.Image] = []
        self.full_sources: List[Image.Image] = []
        self._scaled: Dict[Tuple, Image.Image] = {}
        self._handle_cache: Dict[tuple, ImageTk.PhotoImage] = {}

        self.tile_rects: List[Tuple[float, float, float, float]] = []
        self.project_bounds = (0.0, 0.0, 0.0, 0.0)
        self._display_img: Optional[ImageTk.PhotoImage] = None
        self._sidebar_img: Optional[ImageTk.PhotoImage] = None
        self._last_preview_image: Optional[Image.Image] = None
        self._floating_window: Optional[FloatingPreviewWindow] = None
        self._floating_btn_var = tk.StringVar(value="置顶窗口")
        self._render_job: Optional[str] = None
        self._offset_x = MARGIN
        self._offset_y = MARGIN
        self._frozen_offsets = False
        self._drag_scale_lock: Optional[dict] = None
        self._drag_active = False
        self._quick_export_mode = False
        self._pan_scan_mark: Optional[Tuple[int, int]] = None
        self._sidebar_sync_job: Optional[str] = None

        self.active_tool: Optional[dict] = None
        self.focus_idx: Optional[int] = None
        self.drag_last: Optional[Tuple[int, int]] = None
        self.preview_mode = False
        self.status_var = tk.StringVar(value="请先选择图片")

        self._history_stack: List[dict] = []
        self._history_labels: List[str] = []

        self._build()

    def theme(self):
        return self._app.theme()

    def _build(self):
        t = self.theme()
        self._top_outer = tk.Frame(self, bg=t["bg"], padx=ui(24), pady=ui(14))
        self._top_outer.pack(fill="x")
        self._top = tk.Frame(self._top_outer, bg=t["panel"], highlightthickness=1,
                             highlightbackground=t["border"], padx=ui(16), pady=ui(10))
        self._top.pack(fill="x")
        ttk.Button(self._top, text="‹ 返回", command=self.back_cb, style="Ghost.TButton").pack(side="left", padx=(0, ui(8)))
        self._title = tk.Label(self._top, text="拼接编辑", bg=t["panel"], fg=t["fg"],
                               font="{Microsoft YaHei} 15 bold")
        self._title.pack(side="left", padx=ui(14))
        self._hint = tk.Label(self._top, text="", bg=t["panel"], fg=t["muted"], font="{Microsoft YaHei} 10")
        self._hint.pack(side="left")
        ttk.Button(self._top, text="设置", command=self.settings_cb, style="Ghost.TButton").pack(side="right", padx=(0, ui(8)))
        ttk.Button(self._top, text="自动对齐", command=self._on_auto_stitch_click, style="Ghost.TButton").pack(side="right", padx=(0, ui(8)))
        # 导出下拉框：只有“快速导出为PNG”和“导出”两项
        self._export_cb = ttk.Combobox(
            self._top, values=("快速导出为PNG", "导出"), state="readonly",
            width=14, style="App.TCombobox", font="{Microsoft YaHei} 10")
        self._export_cb.set("快速导出为PNG")
        self._export_cb.pack(side="right", padx=(0, ui(8)))
        self._export_cb.bind("<<ComboboxSelected>>", self._on_export_selected)
        self._export_cb.bind("<ButtonPress-1>", self._on_export_press)

        self._center_outer = tk.Frame(self, bg=t["bg"], padx=ui(18), pady=0)
        self._center_outer.pack(fill="both", expand=True)
        self._paned = tk.PanedWindow(self._center_outer, orient=tk.HORIZONTAL, sashwidth=6,
                                     showhandle=False, bg=t["bg"], bd=0, relief="flat")
        self._paned.pack(fill="both", expand=True)

        self._sidebar = tk.Frame(self._paned, bg=t["panel"], highlightthickness=1,
                                 highlightbackground=t["border"], padx=ui(10), pady=ui(10))
        self._paned.add(self._sidebar, minsize=self._sidebar_min())
        self._preview_card = tk.Frame(self._sidebar, bg=t["panel2"], highlightthickness=1,
                                      highlightbackground=t["border"], padx=ui(8), pady=ui(8))
        self._preview_card.pack(fill="both", expand=True)
        tk.Label(self._preview_card, text="整体预览", bg=t["panel2"], fg=t["fg"],
                 font="{Microsoft YaHei} 11 bold").pack(anchor="w")
        self.preview_canvas = tk.Canvas(self._preview_card, bg=t["canvas"], highlightthickness=0, bd=0, height=ui(280))
        self.preview_canvas.pack(fill="both", expand=True, pady=(ui(8), 0))
        self.preview_canvas.bind("<Configure>", lambda _e: self._refresh_sidebar_preview())

        self._history_card = tk.Frame(self._sidebar, bg=t["panel2"], highlightthickness=1,
                                      highlightbackground=t["border"], padx=ui(8), pady=ui(8))
        self._history_card.pack(fill="both", expand=True, pady=(ui(14), 0))
        tk.Label(self._history_card, text="历史记录", bg=t["panel2"], fg=t["fg"],
                 font="{Microsoft YaHei} 11 bold").pack(anchor="w")
        self.history_list = tk.Listbox(self._history_card, height=12, activestyle="none",
                                       bg=t["panel"], fg=t["fg"], highlightthickness=0,
                                       selectbackground=t["blue"], selectforeground="white",
                                       relief="flat", font="{Microsoft YaHei} 10")
        self.history_list.pack(fill="both", expand=True, pady=(ui(8), 0))

        self._wrap = tk.Frame(self._paned, bg=t["panel"], highlightthickness=1,
                              highlightbackground=t["border"], padx=ui(12), pady=ui(12))
        self._paned.add(self._wrap, minsize=ui(400))
        self.canvas = tk.Canvas(self._wrap, bg=t["canvas"], highlightthickness=0, bd=0)
        self._xscroll = ttk.Scrollbar(self._wrap, orient="horizontal", command=self.canvas.xview, style="App.Horizontal.TScrollbar")
        self._yscroll = ttk.Scrollbar(self._wrap, orient="vertical", command=self.canvas.yview, style="App.Vertical.TScrollbar")
        self.canvas.configure(xscrollcommand=self._xscroll.set, yscrollcommand=self._yscroll.set)
        self._yscroll.pack(side="right", fill="y")
        self._xscroll.pack(side="bottom", fill="x")
        self.canvas.pack(side="left", fill="both", expand=True)

        self._bot_outer = tk.Frame(self, bg=t["bg"], padx=ui(24), pady=ui(14))
        self._bot_outer.pack(fill="x")
        self._bot = tk.Frame(self._bot_outer, bg=t["panel"], highlightthickness=1,
                             highlightbackground=t["border"], padx=ui(12), pady=ui(8))
        self._bot.pack(fill="x")
        for lbl, cmd in [
            ("预览 Tab", self._toggle_preview),
            ("增加拼接线", self._add_seam),
            ("适应窗口", self._fit_window),
            ("撤销 Ctrl+Z", self._undo),
        ]:
            ttk.Button(self._bot, text=lbl, command=cmd, style="Ghost.TButton").pack(side="left", padx=ui(4))
        self._floating_btn = ttk.Button(self._bot, textvariable=self._floating_btn_var,
                                        command=self._toggle_floating_window, style="Ghost.TButton")
        self._floating_btn.pack(side="left", padx=ui(4))
        self._status_lbl = tk.Label(self._bot, textvariable=self.status_var,
                                    bg=t["panel"], fg=t["muted"], font="{Microsoft YaHei} 10")
        self._status_lbl.pack(side="right", padx=ui(10))

        self._ctx_menu = tk.Menu(self, tearoff=False)
        self.canvas.bind("<Button-1>", self._press)
        self.canvas.bind("<B1-Motion>", self._drag)
        self.canvas.bind("<ButtonRelease-1>", self._release)
        self.canvas.bind("<Button-3>", self._right_click)
        self.canvas.bind("<Motion>", self._motion)
        self.canvas.bind("<Leave>", lambda _e: self.canvas.configure(cursor=""))
        self.canvas.bind("<Configure>", lambda _e: self._schedule())
        self.canvas.bind("<MouseWheel>", self._wheel)
        self.canvas.bind("<Control-Button-1>", self._pan_start)
        self.canvas.bind("<Control-B1-Motion>", self._pan_drag)
        self.canvas.bind("<Control-ButtonRelease-1>", self._pan_end)
        self.canvas.bind("<Key>", self._key)
        self.bind_all("<Escape>", self._global_escape, add="+")
        self._paned.bind("<ButtonRelease-1>", lambda _e: self._enforce_sidebar_bounds())
        self._center_outer.bind("<Configure>", lambda _e: self._defer_enforce_sidebar())
        self.after(60, self._init_sidebar_sash)
        self.canvas.focus_set()

    def _init_sidebar_sash(self):
        try:
            self._paned.sash_place(0, ui(320), 1)
        except Exception:
            pass
        self._enforce_sidebar_bounds()

    def _defer_enforce_sidebar(self):
        if self._sidebar_sync_job is not None:
            self.after_cancel(self._sidebar_sync_job)
        self._sidebar_sync_job = self.after(30, self._enforce_sidebar_bounds)

    def _enforce_sidebar_bounds(self):
        self._sidebar_sync_job = None
        try:
            total = self._paned.winfo_width()
            if total <= 20:
                return
            sash_x, _ = self._paned.sash_coord(0)
            target = max(self._sidebar_min(), min(self._sidebar_max(), sash_x))
            self._paned.sash_place(0, target, 1)
        except Exception:
            pass

    def apply_theme(self):
        t = self.theme()
        self.configure(bg=t["bg"])
        self._top_outer.configure(bg=t["bg"])
        self._center_outer.configure(bg=t["bg"])
        self._bot_outer.configure(bg=t["bg"])
        self._paned.configure(bg=t["bg"])
        for frame in (self._top, self._sidebar, self._wrap, self._bot):
            frame.configure(bg=t["panel"], highlightbackground=t["border"])
        for frame in (self._preview_card, self._history_card):
            frame.configure(bg=t["panel2"], highlightbackground=t["border"])
        self._title.configure(bg=t["panel"], fg=t["fg"])
        self._hint.configure(bg=t["panel"], fg=t["muted"])
        self._status_lbl.configure(bg=t["panel"], fg=t["muted"])
        self.canvas.configure(bg=t["canvas"], highlightbackground=t["border"])
        self.preview_canvas.configure(bg=t["canvas"])
        self.history_list.configure(bg=t["panel"], fg=t["fg"], selectbackground=t["blue"], selectforeground="white")
        theme_widget_tree(self._top, t)
        theme_widget_tree(self._sidebar, t)
        theme_widget_tree(self._bot, t)
        self._refresh_sidebar_preview()
        if self._floating_window is not None and self._floating_window.winfo_exists():
            self._floating_window.apply_theme()
        self._schedule(force=True)

    def load(self, work_dir: Path, items: List[ImageItem], orientation: str):
        self.work_dir = work_dir
        self.items = items
        self.orientation = orientation
        self.preview_mode = False
        self.zoom = 1.0
        self.tiles = []
        self.preview_sources = []
        self.full_sources = []
        self._scaled.clear()
        self._history_stack.clear()
        self._history_labels.clear()
        self.active_tool = None
        self.focus_idx = 0
        self.drag_last = None
        self._frozen_offsets = False
        self._drag_scale_lock = None
        self._last_preview_image = None
        self._sidebar_img = None

        valid = []
        for item in items:
            try:
                full = ImageOps.exif_transpose(Image.open(item.path).convert("RGB"))
            except Exception:
                continue
            valid.append((item, full))
        skipped = len(items) - len(valid)
        if not valid:
            self.status_var.set("没有可用的图片（文件可能已损坏或格式不支持）")
            self._toast("没有可用的图片", kind="error")
            self.canvas.delete("all")
            self._display_img = None
            return
        for idx, (_item, full) in enumerate(valid):
            self.full_sources.append(full)
            prev = full.copy()
            prev.thumbnail((2200, 2200), Image.Resampling.LANCZOS)
            self.preview_sources.append(prev)
            self.tiles.append(Tile(idx, 1.0, 0.0, 0.0, 0.0, 0.0))
        if skipped:
            self._toast(f"已跳过 {skipped} 张无法读取的图片", kind="error", duration=2200)

        label = "横向拼接" if orientation == "horizontal" else "竖向拼接"
        self._title.configure(text=label)
        base = f"已跳过 {skipped} 张 | " if skipped else ""
        self.status_var.set(base + "正在自动检测重叠（Picsew 式对齐）… | 滚轮缩放 | Tab 预览 | Ctrl+左键拖动画布")
        self._refresh_history_view()
        self._fit_window()
        self._run_auto_stitch(reset=False)

    def _run_auto_stitch(self, reset: bool = True):
        """在后台线程检测相邻截图的重叠区域（Picsew 式自动拼接）。

        检测本身较慢，放到后台线程；通过 queue 把结果送回主线程应用，
        避免在非主线程直接调用 tkinter API。
        """
        if len(self.tiles) < 2:
            return
        pairs = list(zip(self.full_sources, self.full_sources[1:]))
        orientation = self.orientation
        if reset:
            for tile in self.tiles:
                tile.crop_left = tile.crop_right = tile.crop_top = tile.crop_bottom = 0.0

        q: "queue.Queue" = queue.Queue()
        self._auto_stitch_queue = q
        token = getattr(self, "_auto_stitch_token", 0) + 1
        self._auto_stitch_token = token

        def work():
            results = []
            for i, (a, b) in enumerate(pairs):
                try:
                    results.append((i, detect_stitch_overlap(a, b, orientation)))
                except Exception:
                    results.append((i, None))
            q.put((token, results))

        threading.Thread(target=work, daemon=True).start()
        self._poll_auto_stitch()

    def _poll_auto_stitch(self):
        q = getattr(self, "_auto_stitch_queue", None)
        if q is None:
            return
        try:
            token, results = q.get_nowait()
        except queue.Empty:
            try:
                self.after(60, self._poll_auto_stitch)
            except Exception:
                pass
            return
        self._auto_stitch_queue = None
        # 若期间已加载了新项目，丢弃过期结果
        if token != getattr(self, "_auto_stitch_token", -1):
            return
        try:
            self._apply_auto_stitch(results)
        except Exception:
            pass

    def _apply_auto_stitch(self, results):
        if not self.tiles:
            return
        pairs = [res for res in results
                 if res is not None and res[1] is not None
                 and 0 <= res[0] < len(self.tiles) - 1]
        if not pairs:
            return
        # 先入历史（保存裁剪前的状态），再应用
        self._push_history(f"自动对齐 {len(pairs)} 处")
        for i, (ca, cb) in pairs:
            a = self.tiles[i]
            b = self.tiles[i + 1]
            for key in ("crop_bottom", "crop_right"):
                if key in ca:
                    setattr(a, key, ca[key])
            for key in ("crop_top", "crop_left"):
                if key in cb:
                    setattr(b, key, cb[key])
        self._scaled.clear()
        self._schedule(force=True)
        self.status_var.set(f"已自动对齐 {len(pairs)} 处重叠，可再手动微调 ✂")
        self._toast(f"已自动对齐 {len(pairs)} 处重叠")

    def _on_auto_stitch_click(self):
        if len(self.tiles) < 2:
            return
        self._run_auto_stitch(reset=True)
        self.status_var.set("正在重新自动对齐…")

    def _tile_visible_dims(self, tile_idx: int, tile: Tile) -> Tuple[float, float]:
        src = self.preview_sources[tile.source_idx]
        vis_w = max(0.0, 1.0 - tile.crop_left - tile.crop_right)
        vis_h = max(0.0, 1.0 - tile.crop_top - tile.crop_bottom)
        if vis_w <= 1e-9 or vis_h <= 1e-9:
            return 0.0, 0.0
        return src.width * vis_w, src.height * vis_h

    def _render_tile(self, tile_idx: int, tile: Tile, fw: int, fh: int) -> Image.Image:
        key = (
            tile_idx, tile.source_idx, fw, fh,
            round(tile.crop_left, 5), round(tile.crop_right, 5),
            round(tile.crop_top, 5), round(tile.crop_bottom, 5),
        )
        if key in self._scaled:
            return self._scaled[key]
        if len(self._scaled) > 180:
            for k in list(self._scaled.keys())[:90]:
                del self._scaled[k]
        if fw <= 0 or fh <= 0:
            out = Image.new("RGB", (1, 1), self.theme()["canvas"])
            self._scaled[key] = out
            return out
        src = self.preview_sources[tile.source_idx]
        sw, sh = src.size
        left = int(sw * clamp(tile.crop_left, 0.0, 1.0))
        right = int(sw * (1.0 - clamp(tile.crop_right, 0.0, 1.0)))
        top = int(sh * clamp(tile.crop_top, 0.0, 1.0))
        bottom = int(sh * (1.0 - clamp(tile.crop_bottom, 0.0, 1.0)))
        if right <= left or bottom <= top:
            out = Image.new("RGB", (1, 1), self.theme()["canvas"])
            self._scaled[key] = out
            return out
        rendered = src.crop((left, top, right, bottom)).resize((max(1, fw), max(1, fh)), Image.Resampling.LANCZOS)
        self._scaled[key] = rendered
        return rendered

    def _tile_sizes(self) -> List[Tuple[int, int]]:
        if not self.tiles:
            return []
        dims = []
        for idx, tile in enumerate(self.tiles):
            vw, vh = self._tile_visible_dims(idx, tile)
            dims.append((vw, vh, tile.weight))
        if self.orientation == "horizontal":
            base_h = max(ui(200), self.canvas.winfo_height() - MARGIN * 2)
            raw_ws = []
            for vw, vh, wt in dims:
                if vw <= 0 or vh <= 0:
                    raw_ws.append(0)
                else:
                    raw_ws.append(max(4, int(base_h * (vw / max(1.0, vh)) * wt)))
            avail_w = max(ui(200), self.canvas.winfo_width() - MARGIN * 2)
            fit = self._drag_scale_lock["fit"] if self._drag_scale_lock and self._drag_scale_lock.get("orientation") == "horizontal" else min(1.0, avail_w / max(1, sum(raw_ws) or 1))
            h = max(20, int(base_h * fit * self.zoom))
            sizes = []
            for w, (vw, vh, _wt) in zip(raw_ws, dims):
                if vw <= 0 or vh <= 0 or w <= 0:
                    sizes.append((0, 0))
                else:
                    sizes.append((max(1, int(w * fit * self.zoom)), h))
            return sizes
        base_w = max(ui(200), self.canvas.winfo_width() - MARGIN * 2)
        raw_hs = []
        for vw, vh, wt in dims:
            if vw <= 0 or vh <= 0:
                raw_hs.append(0)
            else:
                raw_hs.append(max(4, int(base_w * (vh / max(1.0, vw)) * wt)))
        avail_h = max(ui(200), self.canvas.winfo_height() - MARGIN * 2)
        fit = self._drag_scale_lock["fit"] if self._drag_scale_lock and self._drag_scale_lock.get("orientation") == "vertical" else min(1.0, avail_h / max(1, sum(raw_hs) or 1))
        w = max(20, int(base_w * fit * self.zoom))
        sizes = []
        for h, (vw, vh, _wt) in zip(raw_hs, dims):
            if vw <= 0 or vh <= 0 or h <= 0:
                sizes.append((0, 0))
            else:
                sizes.append((w, max(1, int(h * fit * self.zoom))))
        return sizes

    def _capture_drag_scale_lock(self):
        if self._drag_scale_lock is not None or not self.tiles:
            return
        dims = []
        for idx, tile in enumerate(self.tiles):
            vw, vh = self._tile_visible_dims(idx, tile)
            dims.append((vw, vh, tile.weight))
        if self.orientation == "horizontal":
            base_h = max(ui(200), self.canvas.winfo_height() - MARGIN * 2)
            raw_ws = [0 if vw <= 0 or vh <= 0 else max(4, int(base_h * (vw / max(1.0, vh)) * wt)) for vw, vh, wt in dims]
            avail_w = max(ui(200), self.canvas.winfo_width() - MARGIN * 2)
            fit = min(1.0, avail_w / max(1, sum(raw_ws) or 1))
        else:
            base_w = max(ui(200), self.canvas.winfo_width() - MARGIN * 2)
            raw_hs = [0 if vw <= 0 or vh <= 0 else max(4, int(base_w * (vh / max(1.0, vw)) * wt)) for vw, vh, wt in dims]
            avail_h = max(ui(200), self.canvas.winfo_height() - MARGIN * 2)
            fit = min(1.0, avail_h / max(1, sum(raw_hs) or 1))
        self._drag_scale_lock = {"orientation": self.orientation, "fit": fit}

    def _segment_positions(self, sizes: List[Tuple[int, int]]) -> List[Tuple[int, int]]:
        def normal_positions():
            pos = []
            if self.orientation == "horizontal":
                cx = 0
                for w, _h in sizes:
                    pos.append((cx, 0))
                    cx += w
            else:
                cy = 0
                for _w, h in sizes:
                    pos.append((0, cy))
                    cy += h
            return pos

        positions = normal_positions()
        tool = self.active_tool or {}
        if not self._drag_scale_lock:
            return positions

        if tool.get("kind") == "seam_crop" and tool.get("target") in ("a", "b"):
            idx = tool["idx"]
            target = tool["target"]
            locked_sizes = tool.get("locked_sizes") or sizes
            if self.orientation == "vertical":
                seam_locked = sum(h for _w, h in locked_sizes[:idx + 1])
                top_total = sum(h for _w, h in sizes[:idx + 1])
                top_start = seam_locked - top_total if target == "a" else 0
                bottom_start = seam_locked
                positions = []
                y = top_start
                for i, (_w, h) in enumerate(sizes):
                    if i <= idx:
                        positions.append((0, y))
                        y += h
                y = bottom_start
                for i, (_w, h) in enumerate(sizes[idx + 1:], start=idx + 1):
                    positions.append((0, y))
                    y += h
                return positions
            seam_locked = sum(w for w, _h in locked_sizes[:idx + 1])
            left_total = sum(w for w, _h in sizes[:idx + 1])
            left_start = seam_locked - left_total if target == "a" else 0
            right_start = seam_locked
            positions = []
            x = left_start
            for i, (w, _h) in enumerate(sizes):
                if i <= idx:
                    positions.append((x, 0))
                    x += w
            x = right_start
            for i, (w, _h) in enumerate(sizes[idx + 1:], start=idx + 1):
                positions.append((x, 0))
                x += w
            return positions

        if tool.get("kind") == "edge":
            ti = tool["tile_idx"]
            side = tool["side"]
            locked_sizes = tool.get("locked_sizes") or sizes
            locked_edge_pos = tool.get("locked_edge_pos")
            positions = normal_positions()
            if self.orientation == "vertical":
                current_total = sum(h for _w, h in sizes)
                locked_total = sum(h for _w, h in locked_sizes)
                if ti == len(sizes) - 1 and side == "bottom":
                    target_edge = locked_edge_pos if locked_edge_pos is not None else locked_total
                    shift = target_edge - current_total
                    positions = [(x, y + shift) for x, y in positions]
                elif ti == 0 and side == "top":
                    target_edge = locked_edge_pos if locked_edge_pos is not None else 0
                    shift = target_edge - positions[0][1]
                    positions = [(x, y + shift) for x, y in positions]
            else:
                current_total = sum(w for w, _h in sizes)
                locked_total = sum(w for w, _h in locked_sizes)
                if ti == len(sizes) - 1 and side == "right":
                    target_edge = locked_edge_pos if locked_edge_pos is not None else locked_total
                    shift = target_edge - current_total
                    positions = [(x + shift, y) for x, y in positions]
                elif ti == 0 and side == "left":
                    target_edge = locked_edge_pos if locked_edge_pos is not None else 0
                    shift = target_edge - positions[0][0]
                    positions = [(x + shift, y) for x, y in positions]
            return positions
        return positions

    def _build_composite(self):
        if not self.tiles:
            return None
        sizes = self._tile_sizes()
        positions = self._segment_positions(sizes)
        min_x = min((x for x, _y in positions), default=0)
        min_y = min((y for _x, y in positions), default=0)
        max_x = max((x + w for (x, _y), (w, _h) in zip(positions, sizes)), default=1)
        max_y = max((y + h for (_x, y), (_w, h) in zip(positions, sizes)), default=1)
        shift_x = -min(0, min_x)
        shift_y = -min(0, min_y)
        bg_hex = self.theme()["canvas"]
        bg_rgb = tuple(int(bg_hex[i:i+2], 16) for i in (1, 3, 5))
        out = Image.new("RGB", (max(1, int(max_x + shift_x)), max(1, int(max_y + shift_y))), bg_rgb)
        rects: List[Tuple[int, int, int, int]] = []
        for i, tile in enumerate(self.tiles):
            fw, fh = sizes[i]
            px, py = positions[i]
            px = int(px + shift_x)
            py = int(py + shift_y)
            if fw > 0 and fh > 0:
                img = self._render_tile(i, tile, fw, fh)
                out.paste(img, (px, py))
            rects.append((px, py, px + fw, py + fh))
        return out, rects

    def _schedule(self, force=False):
        if force and self._render_job is not None:
            self.after_cancel(self._render_job)
            self._render_job = None
        if self._render_job is None:
            self._render_job = self.after(5 if force else RENDER_DELAY, self._render)

    def _render(self):
        self._render_job = None
        built = self._build_composite()
        if built is None:
            return
        img, rects = built
        cw = max(1, self.canvas.winfo_width())
        ch = max(1, self.canvas.winfo_height())
        if not self._frozen_offsets:
            self._offset_x = max(MARGIN, (cw - img.width) // 2)
            self._offset_y = max(MARGIN, (ch - img.height) // 2)
        ox, oy = self._offset_x, self._offset_y
        self.tile_rects = [(x1 + ox, y1 + oy, x2 + ox, y2 + oy) for x1, y1, x2, y2 in rects]
        self.project_bounds = (ox, oy, ox + img.width, oy + img.height)

        self.canvas.delete("all")
        self._display_img = ImageTk.PhotoImage(img)
        self.canvas.create_image(ox, oy, image=self._display_img, anchor="nw")
        if not self._frozen_offsets:
            sr_w = max(cw, img.width + MARGIN * 2)
            sr_h = max(ch, img.height + MARGIN * 2)
            self.canvas.configure(scrollregion=(0, 0, sr_w, sr_h))
        if not self._drag_active:
            # 拖动中跳过整图复制、侧栏与悬浮预览刷新，显著降低卡顿
            self._last_preview_image = img.copy()
            self._refresh_sidebar_preview()
            if self._floating_window is not None and self._floating_window.winfo_exists():
                self._floating_window.update_image(self._last_preview_image)
        if not self.preview_mode:
            self._draw_overlays()

    def _draw_overlays(self):
        t = self.theme()
        handles = []
        for i, (x1, y1, x2, y2) in enumerate(self.tile_rects):
            if i == self.focus_idx:
                self.canvas.create_rectangle(x1, y1, x2, y2, outline=t["blue_soft"], width=2, dash=(5, 4))
            cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
            at = self.active_tool
            def _ae(ti, sd):
                return bool(at and at["kind"] == "edge" and at["tile_idx"] == ti and at["side"] == sd)
            if self.orientation == "vertical":
                if i == 0:
                    handles.append([cx, y1, f"edge:{i}:top", _ae(i, "top")])
                if i == len(self.tile_rects) - 1:
                    handles.append([cx, y2, f"edge:{i}:bottom", _ae(i, "bottom")])
                handles.extend([
                    [x1, cy, f"edge:{i}:left", _ae(i, "left")],
                    [x2, cy, f"edge:{i}:right", _ae(i, "right")],
                ])
            else:
                if i == 0:
                    handles.append([x1, cy, f"edge:{i}:left", _ae(i, "left")])
                if i == len(self.tile_rects) - 1:
                    handles.append([x2, cy, f"edge:{i}:right", _ae(i, "right")])
                handles.extend([
                    [cx, y1, f"edge:{i}:top", _ae(i, "top")],
                    [cx, y2, f"edge:{i}:bottom", _ae(i, "bottom")],
                ])

        for si in range(len(self.tile_rects) - 1):
            self._draw_seam(si, handles)

        if self.active_tool and self.active_tool.get("kind") == "new_seam":
            self._draw_pending_seam(handles)

        for cx, cy, tag, active in self._spread_handles(handles):
            self._handle(cx, cy, tag, active)

    def _spread_handles(self, handles):
        if not handles:
            return handles
        out = [list(h) for h in handles]
        threshold = self._handle_r() * 2 + ui(20)

        def is_horizontal(item):
            tag = item[2]
            return (tag.startswith("edge:") and tag.endswith((":top", ":bottom"))) or tag.startswith(("seam:", "pending_seam"))

        used = set()
        for i in range(len(out)):
            if i in used:
                continue
            cluster = [i]
            used.add(i)
            horiz = is_horizontal(out[i])
            changed = True
            while changed:
                changed = False
                for j in range(len(out)):
                    if j in used or is_horizontal(out[j]) != horiz:
                        continue
                    for k in cluster:
                        dist_main = abs(out[j][0] - out[k][0]) if horiz else abs(out[j][1] - out[k][1])
                        dist_cross = abs(out[j][1] - out[k][1]) if horiz else abs(out[j][0] - out[k][0])
                        if dist_main < threshold and dist_cross < threshold:
                            cluster.append(j)
                            used.add(j)
                            changed = True
                            break
            if len(cluster) > 1:
                self._offset_handle_cluster(out, cluster, "h" if horiz else "v", threshold)
        return out

    def _offset_handle_cluster(self, handles, cluster, mode, threshold):
        center = sum(handles[i][0] if mode == "h" else handles[i][1] for i in cluster) / len(cluster)
        start = center - threshold * (len(cluster) - 1) / 2
        for order, idx in enumerate(cluster):
            if mode == "h":
                handles[idx][0] = start + threshold * order
            else:
                handles[idx][1] = start + threshold * order

    def _handle_photo(self, active: bool) -> ImageTk.PhotoImage:
        """PIL 超采样渲染手柄圆形（✓/✂），抗锯齿。"""
        t = self.theme()
        r = self._handle_r()
        key = (active, r, t["blue"], t["panel"], t["blue_dark"], t["border"], t["fg"])
        ph = self._handle_cache.get(key)
        if ph is not None:
            return ph
        scale = 2
        size = r * 2 * scale
        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        fill = t["blue"] if active else t["panel"]
        outline = t["blue_dark"] if active else t["border"]
        d.ellipse((2 * scale, 2 * scale, size - 2 * scale, size - 2 * scale),
                  fill=fill, outline=outline, width=int(2 * scale))
        text = "✓" if active else "✂"
        try:
            fnt = ImageFont.truetype("seguisym.ttf", int(11 * scale))
        except Exception:
            try:
                fnt = ImageFont.truetype("C:/Windows/Fonts/seguisym.ttf", int(11 * scale))
            except Exception:
                fnt = ImageFont.load_default()
        bbox = d.textbbox((0, 0), text, font=fnt)
        tw_t = bbox[2] - bbox[0]
        th_t = bbox[3] - bbox[1]
        tf = "white" if active else t["fg"]
        d.text(((size - tw_t) // 2 - bbox[0], (size - th_t) // 2 - bbox[1]),
               text, font=fnt, fill=tf)
        img = img.resize((r * 2, r * 2), Image.Resampling.LANCZOS)
        ph = ImageTk.PhotoImage(img)
        self._handle_cache[key] = ph
        return ph

    def _handle(self, cx: float, cy: float, tag: str, active: bool):
        ph = self._handle_photo(active)
        self.canvas.create_image(cx, cy, image=ph, tags=(tag, "ctrl"))

    def _draw_seam(self, si: int, handles=None):
        t = self.theme()
        a = self.tile_rects[si]
        b = self.tile_rects[si + 1]
        active = bool(self.active_tool and self.active_tool.get("kind") == "seam_crop" and self.active_tool.get("idx") == si)
        tag = f"seam:{si}"
        if self.orientation == "horizontal":
            sx = (a[2] + b[0]) / 2
            y1, y2 = self.project_bounds[1], self.project_bounds[3]
            self.canvas.create_line(sx, y1, sx, y2, fill=t["blue_soft"], width=2, dash=(10, 6), tags=(tag, "ctrl"))
            if handles is None:
                self._handle(sx, (y1 + y2) / 2, tag, active)
            else:
                handles.append([sx, (y1 + y2) / 2, tag, active])
        else:
            sy = (a[3] + b[1]) / 2
            x1, x2 = self.project_bounds[0], self.project_bounds[2]
            self.canvas.create_line(x1, sy, x2, sy, fill=t["blue_soft"], width=2, dash=(10, 6), tags=(tag, "ctrl"))
            if handles is None:
                self._handle((x1 + x2) / 2, sy, tag, active)
            else:
                handles.append([(x1 + x2) / 2, sy, tag, active])

    def _draw_pending_seam(self, handles=None):
        t = self.theme()
        pos = self.active_tool["pos"]
        if self.orientation == "horizontal":
            y1, y2 = self.project_bounds[1], self.project_bounds[3]
            self.canvas.create_line(pos, y1, pos, y2, fill=t["blue"], width=3, dash=(12, 6), tags=("pending_seam",))
            if handles is None:
                self._handle(pos, (y1 + y2) / 2, "pending_seam", True)
            else:
                handles.append([pos, (y1 + y2) / 2, "pending_seam", True])
        else:
            x1, x2 = self.project_bounds[0], self.project_bounds[2]
            self.canvas.create_line(x1, pos, x2, pos, fill=t["blue"], width=3, dash=(12, 6), tags=("pending_seam",))
            if handles is None:
                self._handle((x1 + x2) / 2, pos, "pending_seam", True)
            else:
                handles.append([(x1 + x2) / 2, pos, "pending_seam", True])

    def _hit(self, x: float, y: float) -> Optional[dict]:
        d = ui(12)
        for iid in reversed(self.canvas.find_overlapping(x - d, y - d, x + d, y + d)):
            for tag in self.canvas.gettags(iid):
                if tag.startswith("edge:"):
                    _, ti, side = tag.split(":")
                    return {"kind": "edge_btn", "tile_idx": int(ti), "side": side}
                if tag.startswith("seam:") and tag != "pending_seam":
                    return {"kind": "seam_btn", "idx": int(tag.split(":")[1])}
                if tag == "pending_seam":
                    return {"kind": "pending_seam"}
        if self.active_tool and self.active_tool.get("kind") == "seam_crop":
            idx = self.active_tool["idx"]
            if idx < len(self.tile_rects) - 1:
                a = self.tile_rects[idx]
                b = self.tile_rects[idx + 1]
                if self.orientation == "horizontal":
                    sx = (a[2] + b[0]) / 2
                    if min(a[1], b[1]) - 8 <= y <= max(a[3], b[3]) + 8 and min(a[0], b[0]) - 8 <= x <= max(a[2], b[2]) + 8:
                        return {"kind": "seam_side", "idx": idx, "side": "a" if x < sx else "b"}
                else:
                    sy = (a[3] + b[1]) / 2
                    if min(a[0], b[0]) - 8 <= x <= max(a[2], b[2]) + 8 and min(a[1], b[1]) - 8 <= y <= max(a[3], b[3]) + 8:
                        return {"kind": "seam_side", "idx": idx, "side": "a" if y < sy else "b"}
        for i, (x1, y1, x2, y2) in enumerate(self.tile_rects):
            if x1 <= x <= x2 and y1 <= y <= y2:
                return {"kind": "tile", "tile_idx": i}
        return None

    def _motion(self, event):
        cx, cy = self.canvas.canvasx(event.x), self.canvas.canvasy(event.y)
        if self.active_tool and self.active_tool.get("kind") == "new_seam":
            pb = self.project_bounds
            if self.orientation == "horizontal":
                self.active_tool["pos"] = clamp(cx, pb[0] + 5, pb[2] - 5)
                self.canvas.configure(cursor="sb_h_double_arrow")
            else:
                self.active_tool["pos"] = clamp(cy, pb[1] + 5, pb[3] - 5)
                self.canvas.configure(cursor="sb_v_double_arrow")
            self._schedule()
            return
        hit = self._hit(cx, cy)
        cursor = ""
        if hit:
            if hit["kind"] in ("edge_btn", "seam_btn", "pending_seam"):
                cursor = "hand2"
            elif hit["kind"] == "seam_side":
                cursor = "sb_v_double_arrow" if self.orientation == "vertical" else "sb_h_double_arrow"
        self.canvas.configure(cursor=cursor)

    def _global_escape(self, _event=None):
        if self.active_tool and self.active_tool.get("kind") == "new_seam":
            self.active_tool = None
            self.canvas.configure(cursor="")
            self.status_var.set("已取消增加拼接线")
            self._schedule(force=True)
            return "break"
        return None

    def _pan_start(self, event):
        self.canvas.focus_set()
        self._pan_scan_mark = (event.x, event.y)
        self.canvas.scan_mark(event.x, event.y)
        self.canvas.configure(cursor="fleur")
        return "break"

    def _pan_drag(self, event):
        if self._pan_scan_mark is None:
            return "break"
        self.canvas.scan_dragto(event.x, event.y, gain=1)
        return "break"

    def _pan_end(self, _event):
        self._pan_scan_mark = None
        if not (self.active_tool and self.active_tool.get("kind") == "new_seam"):
            self.canvas.configure(cursor="")
        return "break"

    def _press(self, event):
        if event.state & 0x4:
            return
        self.canvas.focus_set()
        x, y = self.canvas.canvasx(event.x), self.canvas.canvasy(event.y)
        hit = self._hit(x, y)
        self.drag_last = (event.x, event.y)
        if not hit:
            return
        kind = hit["kind"]
        if kind == "edge_btn":
            ti, side = hit["tile_idx"], hit["side"]
            same = bool(self.active_tool and self.active_tool.get("kind") == "edge"
                        and self.active_tool["tile_idx"] == ti and self.active_tool["side"] == side)
            if same:
                self.active_tool = None
                self.status_var.set("裁剪已固定 ✓")
            else:
                self._push_history("裁剪调整")
                locked_edge_pos = None
                if 0 <= ti < len(self.tile_rects):
                    rx1, ry1, rx2, ry2 = self.tile_rects[ti]
                    if side == "bottom":
                        locked_edge_pos = ry2 - self._offset_y
                    elif side == "top":
                        locked_edge_pos = ry1 - self._offset_y
                    elif side == "right":
                        locked_edge_pos = rx2 - self._offset_x
                    elif side == "left":
                        locked_edge_pos = rx1 - self._offset_x
                self.active_tool = {
                    "kind": "edge",
                    "tile_idx": ti,
                    "side": side,
                    "locked_sizes": list(self._tile_sizes()),
                    "locked_edge_pos": locked_edge_pos,
                }
                self.focus_idx = ti
                self.status_var.set("拖动鼠标裁剪 | 再次点击 ✓ 固定")
            self._schedule(force=True)
            return
        if kind == "seam_btn":
            idx = hit["idx"]
            same = bool(self.active_tool and self.active_tool.get("kind") == "seam_crop" and self.active_tool["idx"] == idx)
            if same:
                self.active_tool = None
                self.status_var.set("拼接线裁剪已固定 ✓")
            else:
                self._push_history("拼接线裁剪")
                self.active_tool = {"kind": "seam_crop", "idx": idx, "target": None}
                self.status_var.set("点击拼接线两侧区域后拖动裁剪 | 再次点击 ✓ 固定")
            self._schedule(force=True)
            return
        if kind == "seam_side":
            self.active_tool["target"] = hit["side"]
            self.active_tool["locked_sizes"] = list(self._tile_sizes())
            self.focus_idx = hit["idx"] if hit["side"] == "a" else hit["idx"] + 1
            self._schedule(force=True)
            return
        if kind == "pending_seam":
            self._confirm_seam()
            return
        if kind == "tile":
            self.focus_idx = hit["tile_idx"]
            if self.active_tool and self.active_tool.get("kind") == "seam_crop":
                idx = self.active_tool["idx"]
                if hit["tile_idx"] == idx:
                    self.active_tool["target"] = "a"
                    self.active_tool["locked_sizes"] = list(self._tile_sizes())
                elif hit["tile_idx"] == idx + 1:
                    self.active_tool["target"] = "b"
                    self.active_tool["locked_sizes"] = list(self._tile_sizes())
            self._schedule(force=True)

    def _drag(self, event):
        if event.state & 0x4:
            return
        if not self.active_tool or self.drag_last is None:
            return
        dx = event.x - self.drag_last[0]
        dy = event.y - self.drag_last[1]
        if abs(dx) < 0.5 and abs(dy) < 0.5:
            return
        self.drag_last = (event.x, event.y)
        self._capture_drag_scale_lock()
        self._frozen_offsets = True
        self._drag_active = True
        tool = self.active_tool
        if tool["kind"] == "edge":
            self._adjust(tool["tile_idx"], tool["side"], dx, dy)
        elif tool["kind"] == "seam_crop":
            side_key = tool.get("target")
            if side_key is None:
                idx = tool["idx"]
                if self.orientation == "horizontal":
                    sx = (self.tile_rects[idx][2] + self.tile_rects[idx + 1][0]) / 2
                    side_key = "a" if event.x < sx else "b"
                else:
                    sy = (self.tile_rects[idx][3] + self.tile_rects[idx + 1][1]) / 2
                    side_key = "a" if event.y < sy else "b"
                tool["target"] = side_key
            tool.setdefault("locked_sizes", list(self._tile_sizes()))
            ti = tool["idx"] if side_key == "a" else tool["idx"] + 1
            self._adjust(ti, self._seam_side(tool["idx"], side_key), dx, dy)
        elif tool["kind"] == "new_seam":
            self._move_pending(dx, dy)
        self._schedule()

    def _release(self, _event):
        if self._pan_scan_mark is not None:
            self._pan_end(_event)
            return
        had_drag = self.drag_last is not None and self._drag_scale_lock is not None
        active_kind = self.active_tool.get("kind") if self.active_tool else None
        self._frozen_offsets = False
        self._drag_active = False
        self.drag_last = None
        self._drag_scale_lock = None
        if had_drag and active_kind in ("edge", "seam_crop"):
            self._maybe_prompt_delete_tile()
        self._schedule(force=True)
        if not (self.active_tool and self.active_tool.get("kind") == "new_seam"):
            self.canvas.configure(cursor="")

    def _wheel(self, event):
        factor = 1.06 if event.delta > 0 else (1 / 1.06)
        nz = clamp(self.zoom * factor, 0.08, 12.0)
        if abs(nz - self.zoom) > 1e-9:
            self.zoom = nz
            self._scaled.clear()
            self._schedule(force=True)
        return "break"

    def _key(self, event):
        if event.keysym.lower() == "z" and (event.state & 0x4):
            self._undo()
            return
        if event.keysym == "Tab":
            self._toggle_preview()
            return "break"
        if event.keysym == "Escape":
            if self._global_escape(event) == "break":
                return
        if not self.active_tool:
            return
        step = 1
        ks = event.keysym
        tool = self.active_tool
        if tool["kind"] == "edge":
            side = tool["side"]
            if side in ("top", "bottom") and ks in ("Up", "Down"):
                self._adjust(tool["tile_idx"], side, 0, -step if ks == "Up" else step)
                self._schedule()
            elif side in ("left", "right") and ks in ("Left", "Right"):
                self._adjust(tool["tile_idx"], side, -step if ks == "Left" else step, 0)
                self._schedule()
            elif ks in ("Return", "space"):
                self.active_tool = None
                self.status_var.set("裁剪已固定 ✓")
                self._schedule(force=True)
        elif tool["kind"] == "seam_crop":
            side_key = tool.get("target") or "a"
            ti = tool["idx"] if side_key == "a" else tool["idx"] + 1
            edge_side = self._seam_side(tool["idx"], side_key)
            if edge_side in ("top", "bottom") and ks in ("Up", "Down"):
                self._adjust(ti, edge_side, 0, -step if ks == "Up" else step)
                self._schedule()
            elif edge_side in ("left", "right") and ks in ("Left", "Right"):
                self._adjust(ti, edge_side, -step if ks == "Left" else step, 0)
                self._schedule()
            elif ks in ("Return", "space"):
                self.active_tool = None
                self.status_var.set("拼接线裁剪已固定 ✓")
                self._schedule(force=True)
        elif tool["kind"] == "new_seam":
            if self.orientation == "horizontal" and ks in ("Left", "Right"):
                self._move_pending(-step if ks == "Left" else step, 0)
                self._schedule()
            elif self.orientation == "vertical" and ks in ("Up", "Down"):
                self._move_pending(0, -step if ks == "Up" else step)
                self._schedule()
            elif ks in ("Return", "space"):
                self._confirm_seam()

    def _adjust(self, tile_idx: int, side: str, dx: float, dy: float):
        if not (0 <= tile_idx < len(self.tiles)) or tile_idx >= len(self.tile_rects):
            return
        tile = self.tiles[tile_idx]
        x1, y1, x2, y2 = self.tile_rects[tile_idx]
        fw = max(1.0, x2 - x1)
        fh = max(1.0, y2 - y1)
        if side == "top":
            tile.crop_top = clamp(tile.crop_top + (-dy / fh), 0.0, min(1.0, 1.0 - tile.crop_bottom))
        elif side == "bottom":
            tile.crop_bottom = clamp(tile.crop_bottom + (dy / fh), 0.0, min(1.0, 1.0 - tile.crop_top))
        elif side == "left":
            tile.crop_left = clamp(tile.crop_left + (-dx / fw), 0.0, min(1.0, 1.0 - tile.crop_right))
        elif side == "right":
            tile.crop_right = clamp(tile.crop_right + (dx / fw), 0.0, min(1.0, 1.0 - tile.crop_left))
        if abs((tile.crop_left + tile.crop_right) - 1.0) < 1e-6:
            if tile.crop_left > tile.crop_right:
                tile.crop_left = 1.0 - tile.crop_right
            else:
                tile.crop_right = 1.0 - tile.crop_left
        if abs((tile.crop_top + tile.crop_bottom) - 1.0) < 1e-6:
            if tile.crop_top > tile.crop_bottom:
                tile.crop_top = 1.0 - tile.crop_bottom
            else:
                tile.crop_bottom = 1.0 - tile.crop_top
        # 只失效被拖动分块的缓存，其它分块继续复用 → 拖动流畅
        self._invalidate_tile_cache(tile_idx)

    def _invalidate_tile_cache(self, tile_idx: int):
        for k in [k for k in self._scaled if k[0] == tile_idx]:
            del self._scaled[k]

    def _visible_fraction(self, tile: Tile) -> float:
        return max(0.0, 1.0 - tile.crop_left - tile.crop_right) * max(0.0, 1.0 - tile.crop_top - tile.crop_bottom)

    def _maybe_prompt_delete_tile(self):
        if not self.active_tool:
            return
        ti = None
        tool = self.active_tool
        if tool.get("kind") == "edge":
            ti = tool.get("tile_idx")
        elif tool.get("kind") == "seam_crop":
            side_key = tool.get("target")
            if side_key in ("a", "b"):
                ti = tool["idx"] if side_key == "a" else tool["idx"] + 1
        if ti is None or not (0 <= ti < len(self.tiles)):
            return
        if self._visible_fraction(self.tiles[ti]) > 1e-9:
            return
        if messagebox.askyesno("删除这部分？", "当前这部分已经完全不可见。\n要删除这一块图片吗？"):
            self._delete_tile(ti, ask=False)
            self.status_var.set("已删除完全隐藏的图片块")

    def _seam_side(self, seam_idx: int, side_key: str) -> str:
        if self.orientation == "vertical":
            return "bottom" if side_key == "a" else "top"
        return "right" if side_key == "a" else "left"

    def _add_seam(self):
        if not self.tiles:
            return
        self._push_history("添加裁剪线")
        pb = self.project_bounds
        pos = (pb[0] + pb[2]) / 2 if self.orientation == "horizontal" else (pb[1] + pb[3]) / 2
        self.active_tool = {"kind": "new_seam", "pos": pos}
        self.canvas.configure(cursor="sb_v_double_arrow" if self.orientation == "vertical" else "sb_h_double_arrow")
        self.status_var.set("移动虚线到目标位置 | 回车或点 ✓ 分割图片 | Esc 取消")
        self._schedule(force=True)

    def _move_pending(self, dx: float, dy: float):
        if not self.active_tool or self.active_tool.get("kind") != "new_seam":
            return
        pb = self.project_bounds
        if self.orientation == "horizontal":
            self.active_tool["pos"] = clamp(self.active_tool["pos"] + dx, pb[0] + 5, pb[2] - 5)
        else:
            self.active_tool["pos"] = clamp(self.active_tool["pos"] + dy, pb[1] + 5, pb[3] - 5)

    def _confirm_seam(self):
        if not self.active_tool or self.active_tool.get("kind") != "new_seam":
            return
        pos = self.active_tool["pos"]
        target = None
        ratio = 0.5
        for i, (x1, y1, x2, y2) in enumerate(self.tile_rects):
            if self.orientation == "horizontal":
                if x1 <= pos <= x2 and (x2 - x1) > 1:
                    target = i
                    ratio = (pos - x1) / max(1, x2 - x1)
                    break
            else:
                if y1 <= pos <= y2 and (y2 - y1) > 1:
                    target = i
                    ratio = (pos - y1) / max(1, y2 - y1)
                    break
        if target is None:
            target = self.focus_idx or 0
        ratio = clamp(ratio, 0.02, 0.98)
        self._ensure_tile_materialized(target)
        tile = self.tiles[target].copy()
        vis_w = max(0.0, 1.0 - tile.crop_left - tile.crop_right)
        vis_h = max(0.0, 1.0 - tile.crop_top - tile.crop_bottom)
        if self.orientation == "horizontal":
            if vis_w <= 1e-9:
                self.active_tool = None
                self._schedule(force=True)
                return
            cut = vis_w * ratio
            a = tile.copy()
            b = tile.copy()
            a.crop_right = tile.crop_right + max(0.0, vis_w - cut)
            b.crop_left = tile.crop_left + max(0.0, cut)
        else:
            if vis_h <= 1e-9:
                self.active_tool = None
                self._schedule(force=True)
                return
            cut = vis_h * ratio
            a = tile.copy()
            b = tile.copy()
            a.crop_bottom = tile.crop_bottom + max(0.0, vis_h - cut)
            b.crop_top = tile.crop_top + max(0.0, cut)
        self.tiles[target:target + 1] = [a, b]
        self._scaled.clear()
        self.active_tool = None
        self.focus_idx = target + 1
        self.canvas.configure(cursor="")
        self.status_var.set("已按裁剪线分割图片块")
        self._schedule(force=True)

    def _make_snapshot(self, include_sources=False, label="编辑") -> dict:
        snap = {
            "tiles": [t.copy() for t in self.tiles],
            "zoom": self.zoom,
            "active_tool": dict(self.active_tool) if self.active_tool else None,
            "focus_idx": self.focus_idx,
            "include_sources": include_sources,
            "label": label,
        }
        if include_sources:
            snap["full_sources"] = [im.copy() for im in self.full_sources]
        return snap

    def _append_history_entry(self, label: str):
        stamp = datetime.now().strftime("%H:%M:%S")
        self._history_labels.append(f"[{stamp}] {label}")
        if len(self._history_labels) > 200:
            self._history_labels = self._history_labels[-200:]
        self._refresh_history_view()

    def _push_history(self, label="编辑", include_sources=False):
        self._history_stack.append(self._make_snapshot(include_sources=include_sources, label=label))
        if len(self._history_stack) > 60:
            self._history_stack.pop(0)
        self._append_history_entry(label)

    def _rebuild_previews_from_full(self):
        self.preview_sources = []
        for full in self.full_sources:
            prev = full.copy()
            prev.thumbnail((2200, 2200), Image.Resampling.LANCZOS)
            self.preview_sources.append(prev)

    def _refresh_history_view(self):
        self.history_list.delete(0, "end")
        if not self._history_labels:
            self.history_list.insert("end", "暂无历史记录")
            return
        for idx, label in enumerate(reversed(self._history_labels), start=1):
            self.history_list.insert("end", f"{idx}. {label}")

    def _undo(self):
        if not self._history_stack:
            self.status_var.set("没有可撤销的操作")
            return
        snap = self._history_stack.pop()
        label = snap.get("label", "")
        self.tiles = [t.copy() for t in snap["tiles"]]
        self.zoom = snap["zoom"]
        self.active_tool = snap["active_tool"]
        self.focus_idx = snap["focus_idx"]
        if snap.get("include_sources"):
            self.full_sources = [im.copy() for im in snap["full_sources"]]
            self._rebuild_previews_from_full()
        self._scaled.clear()
        self._append_history_entry(f"撤销：{label}" if label else "撤销")
        self.status_var.set(f"已撤销：{label}" if label else "已撤销")
        self._schedule(force=True)

    def _fit_window(self):
        self.zoom = 1.0
        self._scaled.clear()
        self._frozen_offsets = False
        self._schedule(force=True)

    def _toggle_preview(self):
        self.preview_mode = not self.preview_mode
        self.status_var.set("预览模式（标记已隐藏）| 按 Tab 返回编辑" if self.preview_mode else "编辑模式 | Tab 可快速预览")
        self._schedule(force=True)

    def _toast(self, text: str, kind: str = "info", duration: int = 1800):
        t = self.theme()
        bg = t["panel"] if kind == "info" else t["danger"]
        fg = t["fg"] if kind == "info" else "white"
        border = t["border"] if kind == "info" else t["danger"]
        top = tk.Toplevel(self)
        top.overrideredirect(True)
        top.transient(self.winfo_toplevel())
        top.attributes("-topmost", True)
        frame = tk.Frame(top, bg=bg, highlightthickness=1, highlightbackground=border)
        frame.pack(fill="both", expand=True)
        tk.Label(frame, text=text, bg=bg, fg=fg, font="{Microsoft YaHei} 10 bold", padx=ui(16), pady=ui(9)).pack()
        top.update_idletasks()
        root = self.winfo_toplevel()
        x = root.winfo_rootx() + (root.winfo_width() - top.winfo_width()) // 2
        y = root.winfo_rooty() + ui(56)
        top.geometry(f"+{max(0, x)}+{max(0, y)}")
        top.after(duration, top.destroy)

    def _refresh_sidebar_preview(self):
        if self._last_preview_image is None:
            self.preview_canvas.delete("all")
            return
        cw = max(80, self.preview_canvas.winfo_width())
        ch = max(80, self.preview_canvas.winfo_height())
        img = self._last_preview_image.copy()
        img.thumbnail((max(10, cw - 12), max(10, ch - 12)), Image.Resampling.LANCZOS)
        self._sidebar_img = ImageTk.PhotoImage(img)
        self.preview_canvas.delete("all")
        self.preview_canvas.create_image(cw // 2, ch // 2, image=self._sidebar_img, anchor="center")

    def _floating_cache_path(self) -> Path:
        base = Path(tempfile.gettempdir()) / "stitch_preview_cache"
        base.mkdir(parents=True, exist_ok=True)
        stem = (self.work_dir.name if self.work_dir else "session").replace(" ", "_")
        return base / f"{stem}_floating_cache.png"

    def _build_floating_source_image(self) -> Optional[Image.Image]:
        composite = None
        try:
            composite = self._full_composite(scale=0.5)
        except Exception:
            composite = None
        if composite is None and self._last_preview_image is not None:
            composite = self._last_preview_image.copy()
        if composite is None:
            return None
        try:
            cache_path = self._floating_cache_path()
            composite.save(cache_path, "PNG")
            with Image.open(cache_path) as im:
                return im.convert("RGB")
        except Exception:
            return composite.copy()

    def _update_floating_button(self):
        active = bool(self._floating_window is not None and self._floating_window.winfo_exists())
        self._floating_btn_var.set("取消置顶" if active else "置顶窗口")

    def _on_floating_window_closed(self):
        self._floating_window = None
        self._update_floating_button()
        try:
            root = self.winfo_toplevel()
            root.deiconify()
            root.lift()
            root.focus_force()
        except Exception:
            pass

    def _close_floating_window(self):
        if self._floating_window is None or not self._floating_window.winfo_exists():
            self._floating_window = None
            self._update_floating_button()
            return
        try:
            self._floating_window._close()
        except Exception:
            try:
                self._floating_window.destroy()
            finally:
                self._on_floating_window_closed()

    def _toggle_floating_window(self):
        if self._floating_window is not None and self._floating_window.winfo_exists():
            self._close_floating_window()
        else:
            self._open_floating_window()

    def _open_floating_window(self):
        if self._floating_window is None or not self._floating_window.winfo_exists():
            self._floating_window = FloatingPreviewWindow(self, self._on_floating_window_closed, self._build_floating_source_image)
        src_img = self._build_floating_source_image()
        if src_img is not None:
            self._floating_window.update_image(src_img)
        self._update_floating_button()
        try:
            self._floating_window.present()
        except Exception:
            pass
        try:
            root = self.winfo_toplevel()
            root.after(120, root.iconify)
            self._floating_window.after(220, self._floating_window.present)
        except Exception:
            pass

    def _export(self):
        if not self.tiles:
            return
        ExportDialog(self, self._do_export)

    def _gen_output_path(self, fmt: str) -> Path:
        if not self.work_dir:
            raise RuntimeError("当前没有工作目录")
        ext = "jpg" if fmt == "jpeg" else fmt
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        base = self.work_dir / f"拼接结果_{ts}.{ext}"
        if not base.exists():
            return base
        n = 1
        while True:
            cand = self.work_dir / f"拼接结果_{ts}_{n}.{ext}"
            if not cand.exists():
                return cand
            n += 1

    def _do_export(self, cfg: dict):
        fmt = cfg.get("format")
        target_bytes = int(cfg.get("target_bytes", 5_000_000))
        jpeg_hint = int(cfg.get("jpeg_hint", 85))
        if not self.tiles:
            return
        if getattr(self, "_export_queue", None) is not None:
            self._toast("正在导出中，请稍候…")
            return
        ext = "jpg" if fmt == "jpeg" else fmt
        try:
            default_name = self._gen_output_path(fmt).name
        except Exception:
            default_name = f"拼接结果.{ext}"
        qpath = getattr(self._app.settings, "quick_export_path", "") or ""
        if qpath:
            # 已设置导出路径：直接保存，不再弹路径选择
            out_path = Path(qpath) / default_name
            n = 1
            while out_path.exists():
                stem = Path(default_name).stem
                suffix = Path(default_name).suffix
                out_path = Path(qpath) / f"{stem}_{n}{suffix}"
                n += 1
        else:
            out_path = filedialog.asksaveasfilename(
                title="导出图片",
                initialdir=str(self.work_dir) if self.work_dir else None,
                initialfile=default_name,
                defaultextension=f".{ext}",
                filetypes=[("PNG 图片", "*.png"), ("JPG 图片", "*.jpg"),
                           ("WebP 图片", "*.webp"), ("PDF 文档", "*.pdf")],
            )
            if not out_path:
                self._toast("已取消导出")
                return
            out_path = Path(out_path)
        # 快照当前编辑状态，放到后台线程渲染+压缩，界面保持流畅不卡顿
        tiles = [t.copy() for t in self.tiles]
        sources = list(self.full_sources)
        orientation = self.orientation
        bg_hex = self.theme()["canvas"]
        bg_rgb = tuple(int(bg_hex[i:i + 2], 16) for i in (1, 3, 5))
        # 按目标体积先算好初始渲染比例（少渲染像素 → 导出更快）
        init_scale = _estimate_init_scale(sources, tiles, orientation, fmt, target_bytes)

        self._set_exporting(True)
        q: "queue.Queue" = queue.Queue()
        self._export_queue = q

        def work():
            try:
                composite = _render_export(sources, tiles, orientation, init_scale, bg_rgb)
                _save_to_target(composite, fmt, out_path, target_bytes, jpeg_hint)
                q.put((True, out_path, None))
            except Exception as exc:
                q.put((False, None, exc))

        threading.Thread(target=work, daemon=True).start()
        self._poll_export()

    def _poll_export(self):
        q = getattr(self, "_export_queue", None)
        if q is None:
            return
        try:
            ok, out_path, exc = q.get_nowait()
        except queue.Empty:
            try:
                self.after(60, self._poll_export)
            except Exception:
                pass
            return
        self._export_queue = None
        self._export_finished(ok, out_path, exc)

    def _on_export_press(self, event):
        # 点击正文区域直接快速导出；点击右侧箭头才展开下拉框
        if event.x < self._export_cb.winfo_width() - ui(30):
            self.after_idle(self._quick_export)
            return "break"
        return None

    def _on_export_selected(self, _event=None):
        # 快速导出为PNG：直接弹保存路径导出；导出：先选类型/大小再选路径
        if self._export_cb.get() == "导出":
            self._export()
        else:
            self._quick_export()
        self.after(1, lambda: self._export_cb.set("快速导出为PNG"))

    def _quick_export(self, fmt: str = "png"):
        """快速导出：固定低质量 PNG，弹出保存路径后导出。"""
        if not self.tiles:
            return
        if getattr(self, "_export_queue", None) is not None:
            self._toast("正在导出中，请稍候…")
            return
        ext = "jpg" if fmt == "jpeg" else fmt
        try:
            default_name = self._gen_output_path(fmt).name
        except Exception:
            default_name = f"拼接结果.{ext}"
        qpath = getattr(self._app.settings, "quick_export_path", "") or ""
        if qpath:
            # 已设置导出路径：直接保存，不再弹路径选择
            out_path = Path(qpath) / default_name
            n = 1
            while out_path.exists():
                stem = Path(default_name).stem
                suffix = Path(default_name).suffix
                out_path = Path(qpath) / f"{stem}_{n}{suffix}"
                n += 1
        else:
            out_path = filedialog.asksaveasfilename(
                title="快速导出",
                initialfile=default_name,
                defaultextension=f".{ext}",
                filetypes=[("PNG 图片", "*.png"), ("JPG 图片", "*.jpg"),
                           ("WebP 图片", "*.webp"), ("PDF 文档", "*.pdf")],
            )
            if not out_path:
                return
            out_path = Path(out_path)
        # 快速导出固定：低质量（约 1 MB）
        preset = ExportDialog.PRESETS["low"]
        target_bytes = preset["target_bytes"]
        jpeg_hint = preset["jpeg_hint"]

        tiles = [t.copy() for t in self.tiles]
        sources = list(self.full_sources)
        orientation = self.orientation
        bg_hex = self.theme()["canvas"]
        bg_rgb = tuple(int(bg_hex[i:i + 2], 16) for i in (1, 3, 5))
        init_scale = _estimate_init_scale(sources, tiles, orientation, fmt, target_bytes)

        self._quick_export_mode = True
        self._set_exporting(True)
        q: "queue.Queue" = queue.Queue()
        self._export_queue = q

        def work():
            try:
                composite = _render_export(sources, tiles, orientation, init_scale, bg_rgb)
                _save_to_target(composite, fmt, out_path, target_bytes, jpeg_hint)
                q.put((True, out_path, None))
            except Exception as exc:
                q.put((False, None, exc))

        threading.Thread(target=work, daemon=True).start()
        self._poll_export()

    def _set_exporting(self, exporting: bool):
        try:
            self._export_cb.configure(state="disabled" if exporting else "readonly")
        except Exception:
            pass
        if exporting:
            self.status_var.set("正在导出（按目标体积压缩）… 可继续编辑")
        else:
            self.status_var.set("导出完成 ✓  |  滚轮缩放 | Tab 预览 | Ctrl+左键拖动画布")

    def _export_finished(self, ok: bool, out_path: Optional[Path], exc: Optional[Exception]):
        self._set_exporting(False)
        if ok and out_path is not None:
            self._append_history_entry(f"导出：{out_path}")
            self._toast(f"已导出到：{out_path}", duration=1000)
        else:
            self._toast(f"导出失败：{exc}", kind="error", duration=2000)
        self._quick_export_mode = False

    def _export_native_size(self) -> Tuple[int, int]:
        """按原生分辨率估算最终输出的宽高（不渲染像素，速度快，用于导出预估）。"""
        if not self.tiles:
            return 0, 0
        sizes = self._export_tile_sizes()
        positions = self._segment_positions(sizes)
        min_x = min((x for x, _y in positions), default=0)
        min_y = min((y for _x, y in positions), default=0)
        max_x = max((x + w for (x, _y), (w, _h) in zip(positions, sizes)), default=1)
        max_y = max((y + h for (_x, y), (_w, h) in zip(positions, sizes)), default=1)
        return max(1, int(max_x - min_x)), max(1, int(max_y - min_y))

    def _export_tile_sizes(self) -> List[Tuple[int, int]]:
        """按源图原生分辨率计算每块尺寸（与画布大小无关）。

        这是导出清晰的关键：从原始像素渲染，而不是把低分辨率预览放大。
        """
        if not self.tiles:
            return []
        dims = []
        for tile in self.tiles:
            src = self.full_sources[tile.source_idx]
            vis_w = max(0.0, 1.0 - tile.crop_left - tile.crop_right)
            vis_h = max(0.0, 1.0 - tile.crop_top - tile.crop_bottom)
            if vis_w <= 1e-9 or vis_h <= 1e-9:
                dims.append((0, 0, tile.weight))
            else:
                dims.append((int(round(src.width * vis_w)),
                             int(round(src.height * vis_h)), tile.weight))
        if self.orientation == "horizontal":
            base_h = max((h for _w, h, _wt in dims if h > 0), default=1)
            sizes = []
            for w, h, wt in dims:
                if w <= 0 or h <= 0:
                    sizes.append((0, 0))
                else:
                    sizes.append((max(1, int(round(base_h * (w / max(1, h)) * wt))), base_h))
            return sizes
        base_w = max((w for w, _h, _wt in dims if w > 0), default=1)
        sizes = []
        for w, h, wt in dims:
            if w <= 0 or h <= 0:
                sizes.append((0, 0))
            else:
                sizes.append((base_w, max(1, int(round(base_w * (h / max(1, w)) * wt)))))
        return sizes

    def _full_composite(self, scale: float = 1.0) -> Optional[Image.Image]:
        if not self.tiles:
            return None
        bg_hex = self.theme()["canvas"]
        bg_rgb = tuple(int(bg_hex[i:i + 2], 16) for i in (1, 3, 5))
        return _render_export(list(self.full_sources), [t.copy() for t in self.tiles],
                              self.orientation, scale, bg_rgb)

    def _ensure_tile_materialized(self, tile_idx: int):
        if not (0 <= tile_idx < len(self.tiles)):
            return
        tile = self.tiles[tile_idx]
        src = self.full_sources[tile.source_idx]
        left = int(src.width * clamp(tile.crop_left, 0.0, 1.0))
        right = int(src.width * (1.0 - clamp(tile.crop_right, 0.0, 1.0)))
        top = int(src.height * clamp(tile.crop_top, 0.0, 1.0))
        bottom = int(src.height * (1.0 - clamp(tile.crop_bottom, 0.0, 1.0)))
        if right <= left or bottom <= top:
            return
        new_full = src.crop((left, top, right, bottom)).copy()
        self.full_sources.append(new_full)
        new_preview = new_full.copy()
        new_preview.thumbnail((2200, 2200), Image.Resampling.LANCZOS)
        self.preview_sources.append(new_preview)
        tile.source_idx = len(self.full_sources) - 1
        tile.crop_left = tile.crop_right = tile.crop_top = tile.crop_bottom = 0.0

    def _delete_tile(self, tile_idx: int, ask=True):
        if not (0 <= tile_idx < len(self.tiles)):
            return
        if ask and not messagebox.askyesno("删除照片", "确定删除这个图片块吗？"):
            return
        self._push_history("删除图片块")
        del self.tiles[tile_idx]
        if not self.tiles:
            self.active_tool = None
            self.focus_idx = None
        else:
            self.focus_idx = max(0, min(tile_idx, len(self.tiles) - 1))
        self._scaled.clear()
        self.status_var.set("已删除图片块")
        self._schedule(force=True)

    def _rotate_tile(self, tile_idx: int, clockwise=True):
        if not (0 <= tile_idx < len(self.tiles)):
            return
        self._push_history("旋转图片块", include_sources=True)
        self._ensure_tile_materialized(tile_idx)
        tile = self.tiles[tile_idx]
        img = self.full_sources[tile.source_idx].rotate(-90 if clockwise else 90, expand=True)
        self.full_sources[tile.source_idx] = img
        prev = img.copy()
        prev.thumbnail((2200, 2200), Image.Resampling.LANCZOS)
        self.preview_sources[tile.source_idx] = prev
        self._scaled.clear()
        self.focus_idx = tile_idx
        self.status_var.set("已旋转图片块")
        self._schedule(force=True)

    def _open_doodle(self, tile_idx: int):
        if not (0 <= tile_idx < len(self.tiles)):
            return
        self._push_history("涂鸦编辑", include_sources=True)
        self._ensure_tile_materialized(tile_idx)
        tile = self.tiles[tile_idx]
        src_idx = tile.source_idx

        def on_confirm(new_img: Image.Image):
            self.full_sources[src_idx] = new_img
            prev = new_img.copy()
            prev.thumbnail((2200, 2200), Image.Resampling.LANCZOS)
            self.preview_sources[src_idx] = prev
            self._scaled.clear()
            self.focus_idx = tile_idx
            self.status_var.set("已应用涂鸦")
            self._schedule(force=True)

        DoodleDialog(self, self.full_sources[src_idx], on_confirm)

    def _right_click(self, event):
        x, y = self.canvas.canvasx(event.x), self.canvas.canvasy(event.y)
        hit = self._hit(x, y)
        if not hit or hit["kind"] != "tile":
            return
        tile_idx = hit["tile_idx"]
        self.focus_idx = tile_idx
        self._ctx_menu.delete(0, "end")
        self._ctx_menu.add_command(label="删除照片", command=lambda i=tile_idx: self._delete_tile(i))
        self._ctx_menu.add_command(label="向左旋转90°", command=lambda i=tile_idx: self._rotate_tile(i, clockwise=False))
        self._ctx_menu.add_command(label="向右旋转90°", command=lambda i=tile_idx: self._rotate_tile(i, clockwise=True))
        self._ctx_menu.add_separator()
        self._ctx_menu.add_command(label="涂鸦", command=lambda i=tile_idx: self._open_doodle(i))
        self._ctx_menu.tk_popup(event.x_root, event.y_root)


# ────────────────────────────────────────────────── App ──────────────────────

class App(tk.Tk):
    def __init__(self):
        if TkinterDnD is not None:
            TkinterDnD.Tk.__init__(self)
        else:
            super().__init__()
        # 高分屏适配：以“窗口实际所在显示器”的 DPI 为准，而不是主显示器。
        # 应用若被放到不同缩放的副屏，启动时也能按正确比例缩放。
        self._dpi_watch_job: Optional[str] = None
        self._last_dpi: int = 0
        try:
            hwnd = int(self.winfo_id(), 0) if self.winfo_id() else 0
            win_dpi = _get_window_dpi(hwnd)
            self._last_dpi = win_dpi
            if win_dpi > 0:
                global UI_SCALE
                UI_SCALE = win_dpi / 96.0
                _refresh_ui_constants()
        except Exception:
            pass
        # Tk 点-像素换算：1 点 = 1/72 英寸。DPI 感知进程里 winfo_fpixels("1i")
        # 返回的是窗口当前生效 DPI，用它设置 tk scaling 让文字随缩放同步变大。
        try:
            phys_dpi = float(self.winfo_fpixels("1i")) or _get_system_dpi() or 96.0
            self.tk.call("tk", "scaling", max(1.0, phys_dpi / 72.0))
        except Exception:
            pass
        self.settings = AppSettings()
        self._load_settings()
        self.title(APP_NAME)
        # 初始窗口尽量铺满但不超出工作区（高分屏小屏也能完整显示）
        try:
            scr_w = self.winfo_screenwidth()
            scr_h = self.winfo_screenheight()
        except Exception:
            scr_w, scr_h = 0, 0
        win_w = min(ui(1300), max(ui(900), scr_w - ui(80))) if scr_w else ui(1300)
        win_h = min(ui(900), max(ui(620), scr_h - ui(120))) if scr_h else ui(900)
        self.geometry(f"{win_w}x{win_h}")
        self.minsize(ui(1000), ui(700))
        self._style = ttk.Style(self)
        self.selection = SelectionGrid(self, self._open_editor, self._open_settings)
        self.editor = StitcherEditor(self, self._show_selection, self._open_settings)
        self.selection.pack(fill="both", expand=True)
        self.editor.pack_forget()
        if TkinterDnD is not None:
            try:
                self.drop_target_register(DND_FILES)
                self.dnd_bind("<<Drop>>", self._on_drop)
            except Exception:
                pass
        self._apply_window_icon()
        self._apply_theme()
        # 周期检测窗口跨显示器拖动时的 DPI 变化（不同缩放比例的显示器间切换）
        self._dpi_watch_job = self.after(800, self._watch_dpi)

    def _on_drop(self, event):
        """支持把文件夹（或图片文件）直接拖进窗口载入。"""
        try:
            import re
            data = (getattr(event, "data", "") or "").strip()
            if not data:
                return
            parts = [a or b for a, b in re.findall(r"\{([^}]*)\}|(\S+)", data)]
            if not parts:
                return
            p = Path(parts[0].replace("\\", "/"))
            if not p.exists():
                return
            self.selection._load_dir(p if p.is_dir() else p.parent)
        except Exception:
            pass

    def _watch_dpi(self):
        """窗口所在显示器 DPI 变化时，按新缩放重算字体与布局。"""
        try:
            hwnd = int(self.winfo_id(), 0) if self.winfo_id() else 0
            dpi = _get_window_dpi(hwnd)
            if dpi and dpi != self._last_dpi:
                self._last_dpi = dpi
                self._on_dpi_changed(dpi)
        except Exception:
            pass
        try:
            self._dpi_watch_job = self.after(800, self._watch_dpi)
        except Exception:
            self._dpi_watch_job = None

    def _on_dpi_changed(self, dpi: int):
        global UI_SCALE
        UI_SCALE = dpi / 96.0
        _refresh_ui_constants()
        try:
            self.tk.call("tk", "scaling", max(1.0, dpi / 72.0))
        except Exception:
            pass
        try:
            self._apply_theme()
        except Exception:
            pass
        # 非最大化时按新缩放重算窗口尺寸，避免在新屏幕上偏小/偏大
        try:
            if str(self.state()) != "zoomed":
                scr_w = self.winfo_screenwidth()
                scr_h = self.winfo_screenheight()
                win_w = min(ui(1300), max(ui(900), scr_w - ui(80))) if scr_w else ui(1300)
                win_h = min(ui(900), max(ui(620), scr_h - ui(120))) if scr_h else ui(900)
                self.geometry(f"{win_w}x{win_h}")
        except Exception:
            pass

    def theme(self) -> Dict[str, str]:
        return THEMES["dark" if self.settings.dark_mode else "light"]

    def _build_app_icon(self):
        size = 256
        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        draw.rounded_rectangle((6, 6, size - 6, size - 6), radius=52, fill="#2563eb")
        x0 = (size - 128) // 2
        top, bot = 58, size - 58
        draw.rounded_rectangle((x0, top, x0 + 52, bot), radius=14, fill="white")
        draw.rounded_rectangle((x0 + 76, top, x0 + 128, bot), radius=14, fill="#bfdbfe")
        for y in range(top + 22, bot - 12, 26):
            draw.rectangle((x0 + 64, y, x0 + 64, y + 12), fill="#ffffff")
        return ImageTk.PhotoImage(img)

    def _apply_window_icon(self):
        try:
            self._icon_photo = self._build_app_icon()
            self.iconphoto(True, self._icon_photo)
        except Exception:
            self._icon_photo = None

    def _init_style(self):
        try:
            self._style.theme_use("clam")
        except Exception:
            pass
        t = self.theme()
        self.option_add("*Font", "{Microsoft YaHei} 10")
        self._style.configure("TButton",
                              background=t["panel2"], foreground=t["fg"],
                              bordercolor=t["border"], darkcolor=t["panel2"],
                              lightcolor=t["panel2"], relief="flat",
                              focusthickness=0, focuscolor=t["panel2"],
                              padding=(ui(12), ui(8)))
        self._style.map("TButton",
                        background=[("active", t["blue"]), ("pressed", t["blue_dark"])],
                        foreground=[("active", "white"), ("pressed", "white")])
        # 主操作按钮：品牌蓝底白字，视觉上更突出
        self._style.configure("Accent.TButton",
                              background=t["blue"], foreground="white",
                              bordercolor=t["blue_dark"], darkcolor=t["blue"],
                              lightcolor=t["blue"], relief="flat",
                              focusthickness=0, focuscolor=t["blue"],
                              padding=(ui(14), ui(8)))
        self._style.map("Accent.TButton",
                        background=[("active", t["blue_soft"]), ("pressed", t["blue_dark"])],
                        foreground=[("active", "white"), ("pressed", "white")])
        # 次级按钮：浅底细边（幽灵按钮），飞书/微信式
        self._style.configure("Ghost.TButton",
                              background=t["panel2"], foreground=t["fg"],
                              bordercolor=t["border"], darkcolor=t["panel2"],
                              lightcolor=t["panel2"], relief="flat",
                              focusthickness=0, focuscolor=t["border"],
                              padding=(ui(12), ui(7)))
        self._style.map("Ghost.TButton",
                        background=[("active", t["border"]), ("pressed", t["border"])],
                        foreground=[("active", t["fg"]), ("pressed", t["fg"])])
        # 下拉框：与主题面板/边框一致，聚焦时蓝色描边
        self._style.configure("App.TCombobox",
                              fieldbackground=t["panel"], background=t["panel"],
                              foreground=t["fg"], arrowcolor=t["fg"],
                              bordercolor=t["border"], lightcolor=t["panel"],
                              darkcolor=t["panel"], relief="flat",
                              padding=(ui(8), ui(4)))
        self._style.map("App.TCombobox",
                        fieldbackground=[("readonly", t["panel"])],
                        background=[("readonly", t["panel"])],
                        foreground=[("readonly", t["fg"])],
                        bordercolor=[("focus", t["blue"]), ("active", t["border"])])
        self.option_add("*TCombobox*Listbox.background", t["panel"])
        self.option_add("*TCombobox*Listbox.foreground", t["fg"])
        self.option_add("*TCombobox*Listbox.selectBackground", t["blue"])
        self.option_add("*TCombobox*Listbox.selectForeground", "white")
        self.option_add("*TCombobox*Listbox.font", "{Microsoft YaHei} 10")
        # 单选按钮：与主题一致，选中指示为蓝色
        self._style.configure("App.TRadiobutton",
                              background=t["panel2"], foreground=t["fg"],
                              font="{Microsoft YaHei} 10")
        self._style.map("App.TRadiobutton",
                        background=[("active", t["panel2"])],
                        foreground=[("active", t["fg"])],
                        indicatorcolor=[("selected", t["blue"])])
        sb_bg = t["panel2"] if not self.settings.dark_mode else t["panel"]
        sb_thumb = "#c9ced6" if not self.settings.dark_mode else "#4a5364"
        self._style.configure("App.Vertical.TScrollbar",
                              background=sb_thumb, troughcolor=sb_bg,
                              bordercolor=sb_bg, arrowcolor=t["fg"],
                              darkcolor=sb_thumb, lightcolor=sb_thumb,
                              gripcount=0, relief="flat", arrowsize=ui(14), width=ui(14))
        self._style.map("App.Vertical.TScrollbar",
                        background=[("active", t["blue"]), ("pressed", t["blue_dark"])],
                        troughcolor=[("active", sb_bg)])
        self._style.configure("App.Horizontal.TScrollbar",
                              background=sb_thumb, troughcolor=sb_bg,
                              bordercolor=sb_bg, arrowcolor=t["fg"],
                              darkcolor=sb_thumb, lightcolor=sb_thumb,
                              gripcount=0, relief="flat", arrowsize=ui(14), width=ui(14))
        self._style.map("App.Horizontal.TScrollbar",
                        background=[("active", t["blue"]), ("pressed", t["blue_dark"])],
                        troughcolor=[("active", sb_bg)])

    def _apply_theme(self):
        self._init_style()
        self.configure(bg=self.theme()["bg"], highlightbackground=self.theme()["bg"])
        try:
            self.tk_setPalette(background=self.theme()["bg"], foreground=self.theme()["fg"], activeBackground=self.theme()["panel2"], activeForeground=self.theme()["fg"])
        except Exception:
            pass
        self.selection.apply_theme()
        self.editor.apply_theme()

    def _settings_path(self) -> Path:
        try:
            return Path(__file__).resolve().parent / "settings.json"
        except Exception:
            return Path.home() / "PicaCrop_settings.json"

    def _load_settings(self):
        try:
            import json
            data = json.loads(self._settings_path().read_text(encoding="utf-8"))
            for k, v in data.items():
                if hasattr(self.settings, k):
                    setattr(self.settings, k, v)
        except Exception:
            pass

    def _save_settings(self):
        try:
            import json
            self._settings_path().write_text(
                json.dumps(self.settings.__dict__, ensure_ascii=False, indent=2),
                encoding="utf-8")
        except Exception:
            pass

    def _on_settings_saved(self):
        self._apply_theme()
        self._save_settings()

    def _open_settings(self):
        SettingsDialog(self, self.settings, self._on_settings_saved)

    def _show_selection(self):
        self.editor.pack_forget()
        self.selection.pack(fill="both", expand=True)

    def _open_editor(self, work_dir: Path, items: List[ImageItem], orientation: str):
        self.selection.pack_forget()
        self.editor.pack(fill="both", expand=True)
        self.editor.load(work_dir, items, orientation)


if __name__ == "__main__":
    app = App()
    app.mainloop()
