"""Export a PDF's existing text layer to one Markdown source draft."""
from __future__ import annotations

import base64
from dataclasses import dataclass
from pathlib import Path
import re
from statistics import median
import unicodedata

import pymupdf


APPENDIX = """## 附录

### 逐句译文

> 待填写

### 词典与语境义

> 待填写

### 词组

> 待填写

### 答案与解析

> 待填写
"""

PROMOTIONAL_FOOTER = re.compile(
    r"懒笔记[·•・|]?https?://english-exam\.lazynote\.cn/\S*?[·•・|]?在线使用效果更好[。.]?"
    r"|https?://zhenti\.burningvocabulary\.(?:cn|com)/?"
    r"|poweredbytcpdf\((?:https?://)?www\.tcpdf\.org/?\)",
    re.IGNORECASE,
)



NON_EXAM_AUDIO = re.compile(
    r"^(?:[▶►▷]\s*)?在线收听本套听力音频[：:]?$"
    r"|^(?:录音原声不在纸面|仅含听力卷面题号与选项|手机或电脑访问下方页面|(?:即可)?在线播放完整听力原声)"
    r"|^https?://english-exam\.lazynote\.cn(?:/|$)"
    r"|^提示[：:].*(?:听力.*(?:相同|一致|重复|两套)|两套听力)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Conversion:
    path: Path
    page_count: int
    warnings: tuple[str, ...]
    ignored_footers: int


@dataclass(frozen=True)
class PrintedLine:
    text: str
    rect: pymupdf.Rect
    baseline: float
    size: float


def printed_lines(blocks):
    for block in blocks:
        if block["type"] != 0:
            continue
        for line in block["lines"]:
            spans = line["spans"]
            text = "".join(span["text"] for span in spans).strip()
            if text:
                first = spans[0]
                yield PrintedLine(text, pymupdf.Rect(line["bbox"]), first["origin"][1], first["size"])


def is_promotional_footer(line, page_height):
    if line.rect.y0 < page_height * 0.84:
        return False
    text = re.sub(r"\s+", "", unicodedata.normalize("NFKC", line.text))
    return PROMOTIONAL_FOOTER.fullmatch(text) is not None


def page_text(lines):
    """Keep printed rows and place same-baseline columns left to right."""
    if not lines:
        return ""
    rows = []
    for line in sorted(lines, key=lambda item: (item.baseline, item.rect.x0)):
        if rows and abs(line.baseline - rows[-1][0].baseline) <= 2 and all(
            min(line.rect.x1, other.rect.x1) - max(line.rect.x0, other.rect.x0) <= 1
            for other in rows[-1]
        ):
            rows[-1].append(line)
        else:
            rows.append([line])

    left = min(line.rect.x0 for line in lines)
    cell_width = max(2, median(line.size for line in lines) / 2)
    result = []
    for row in rows:
        text = ""
        for line in sorted(row, key=lambda item: item.rect.x0):
            column = round((line.rect.x0 - left) / cell_width)
            gap = max(2 if text else 0, column - len(text))
            text += " " * gap + line.text
        result.append(text)
    return "\n".join(result)


def text_fence(text):
    longest = max((len(item) for item in re.findall(chr(96) + "+", text)), default=0)
    fence = chr(96) * max(3, longest + 1)
    return f"{fence}text\n{text}\n{fence}"


def inline_picture(block, page_number, number):
    image = block["image"]
    mime = {"png": "png", "jpeg": "jpeg", "jpg": "jpeg", "webp": "webp"}.get(block["ext"])
    if not mime:
        pixmap = pymupdf.Pixmap(image)
        if pixmap.colorspace and pixmap.colorspace.n > 3:
            pixmap = pymupdf.Pixmap(pymupdf.csRGB, pixmap)
        image = pixmap.tobytes("png")
        mime = "png"
    data = base64.b64encode(image).decode("ascii")
    return f"![第 {page_number} 页插图 {number}](data:image/{mime};base64,{data})"


def is_visually_blank(page):
    # White background plus rendered annotations makes this a visual check,
    # not an assumption that a missing text layer means an empty page.
    pixels = page.get_pixmap(matrix=pymupdf.Matrix(2, 2), colorspace=pymupdf.csGRAY, alpha=False, annots=True)
    return not pixels.samples.strip(b"\xff")


def markdown_from_pdf(pdf_path):
    pdf_path = Path(pdf_path)
    sections = [f"# {pdf_path.stem}", "## 原卷"]
    warnings = []
    missing = []
    ignored_footers = 0

    with pymupdf.open(pdf_path) as document:
        if not document.is_pdf or not document.page_count:
            raise ValueError("文件不是有效的 PDF，或没有页面。")
        if document.needs_pass:
            raise ValueError("PDF 需要密码。请先在本地解锁，再转换。")
        count = document.page_count
        for page in document:
            number = page.number + 1
            blocks = page.get_text("dict", sort=False)["blocks"]
            lines = list(printed_lines(blocks))
            if not lines:
                if is_visually_blank(page):
                    sections.extend((f"### 第 {number} 页", "（原卷空白页）"))
                    warnings.append(f"第 {number} 页为空白页，已保留原页码。")
                else:
                    missing.append(number)
                continue
            visible_lines = [
                line for line in lines
                if not is_promotional_footer(line, page.cropbox.height)
                and not NON_EXAM_AUDIO.search(unicodedata.normalize("NFKC", line.text.strip()))
            ]
            ignored_footers += len(lines) - len(visible_lines)
            sections.extend((f"### 第 {number} 页", text_fence(page_text(visible_lines))))

            picture_number = 0
            has_page_scan = False
            for block in blocks:
                if block["type"] != 1:
                    continue
                bounds = pymupdf.Rect(block["bbox"])
                if bounds.get_area() >= page.rect.get_area() * 0.75:
                    has_page_scan = True
                    continue
                picture_number += 1
                try:
                    sections.append(inline_picture(block, number, picture_number))
                except (RuntimeError, ValueError) as error:
                    warnings.append(f"第 {number} 页插图 {picture_number} 无法导出，请对照原卷确认（{error}）。")
            if has_page_scan:
                warnings.append(f"第 {number} 页存在大幅底图。已保留文字层，扫描底图内的独立图题请对照原卷。")
            if any("\ufffd" in line.text for line in visible_lines):
                warnings.append(f"第 {number} 页文字层包含无法辨认的字符，请对照原卷核对。")
            if page.rotation:
                warnings.append(f"第 {number} 页带有旋转设置，请对照原卷确认阅读顺序。")

    if missing:
        pages = "、".join(map(str, missing))
        raise ValueError(f"第 {pages} 页有可见内容，但没有可提取的文字层，无法生成完整 Markdown。")
    return "\n\n".join((*sections, APPENDIX)), count, tuple(warnings), ignored_footers


def convert_pdf(pdf_path, output_folder):
    pdf_path = Path(pdf_path)
    markdown, page_count, warnings, ignored_footers = markdown_from_pdf(pdf_path)
    output = Path(output_folder)
    output.mkdir(parents=True, exist_ok=True)

    for version in range(1, 10000):
        suffix = "" if version == 1 else f" ({version})"
        target = output / f"{pdf_path.stem}{suffix}.md"
        try:
            stream = target.open("x", encoding="utf-8", newline="\n")
        except FileExistsError:
            continue
        with stream:
            stream.write(markdown)
        return Conversion(target, page_count, warnings, ignored_footers)
    raise FileExistsError(f"目录内已有过多同名结果：{pdf_path.stem}")

