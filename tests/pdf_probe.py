"""Small, dependency-free PDF probe used by the A4 rendering tests.

It understands the subset Qt's QPdfWriter emits: plain indirect objects,
FlateDecode content streams, Type0/Identity-H fonts, and ToUnicode CMaps.
It is deliberately a test helper, not a general PDF library.
"""

from __future__ import annotations

import re
import unicodedata
import zlib
from dataclasses import dataclass
from pathlib import Path


_ARABIC_REPLACEMENTS = {
    "\u06cc": "\u064a",
    "\u06a9": "\u0643",
    "\u06be": "\u0647",
    "\u06c1": "\u0647",
    "\u0640": "",
}


def normalize_arabic(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text)
    for source, target in _ARABIC_REPLACEMENTS.items():
        normalized = normalized.replace(source, target)
    return normalized


@dataclass(frozen=True)
class PdfReport:
    page_count: int
    media_boxes: tuple[tuple[float, float], ...]
    page_texts: tuple[str, ...]

    @property
    def text(self) -> str:
        return "\n".join(self.page_texts)

    @property
    def normalized_text(self) -> str:
        return normalize_arabic(self.text)


def _objects(data: bytes) -> dict[int, bytes]:
    return {
        int(match.group(1)): match.group(2)
        for match in re.finditer(rb"(\d+)\s+0\s+obj(.*?)endobj", data, re.S)
    }


def _stream(body: bytes) -> bytes:
    if b"stream" not in body:
        return b""
    raw = body.split(b"stream", 1)[1].lstrip(b"\r\n")
    raw = raw.rsplit(b"endstream", 1)[0]
    if b"/FlateDecode" in body.split(b"stream", 1)[0]:
        try:
            return zlib.decompress(raw)
        except zlib.error:
            return b""
    return raw


def _hex_text(value: str) -> str:
    value = value.strip()
    if len(value) % 4 == 0:
        try:
            return bytes.fromhex(value).decode("utf-16-be")
        except (ValueError, UnicodeDecodeError):
            pass
    try:
        return bytes.fromhex(value).decode("latin-1")
    except ValueError:
        return ""


def _cmap(mapping: dict[int, str], body: bytes) -> None:
    text = _stream(body).decode("latin-1", errors="replace")
    for block in re.findall(r"beginbfchar(.*?)endbfchar", text, re.S):
        for match in re.finditer(r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>", block):
            width = max(1, len(match.group(1)) // 2)
            mapping[int(match.group(1), 16)] = _hex_text(match.group(2))
            mapping.setdefault("_width", width)
    for block in re.findall(r"beginbfrange(.*?)endbfrange", text, re.S):
        for match in re.finditer(
            r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*(\[(.*?)\]|<([0-9A-Fa-f]+)>)",
            block, re.S,
        ):
            start = int(match.group(1), 16)
            end = int(match.group(2), 16)
            width = max(1, len(match.group(1)) // 2)
            mapping.setdefault("_width", width)
            if match.group(5) is not None:
                base = match.group(5)
                for offset, code in enumerate(range(start, end + 1)):
                    raw = int(base, 16) + offset
                    mapping[code] = chr(raw) if raw <= 0x10FFFF else ""
            else:
                targets = re.findall(r"<([0-9A-Fa-f]+)>", match.group(4))
                for offset, target in enumerate(targets):
                    mapping[start + offset] = _hex_text(target)


def _font_maps(objects: dict[int, bytes], page_body: bytes) -> dict[str, dict]:
    resources = page_body
    resource_match = re.search(rb"/Resources\s+(\d+)\s+0\s+R", page_body)
    if resource_match:
        resources = objects.get(int(resource_match.group(1)), b"")
    font_section = re.search(rb"/Font\s*<<(.*?)>>", resources, re.S)
    if not font_section:
        return {}
    result: dict[str, dict] = {}
    for name, font_id in re.findall(rb"/(F\d+)\s+(\d+)\s+0\s+R", font_section.group(1)):
        body = objects.get(int(font_id), b"")
        unicode_match = re.search(rb"/ToUnicode\s+(\d+)\s+0\s+R", body)
        if not unicode_match:
            continue
        mapping: dict = {}
        _cmap(mapping, objects.get(int(unicode_match.group(1)), b""))
        result[name.decode("ascii")] = mapping
    return result


def _decode_hex_string(value: str, mapping: dict) -> str:
    width = int(mapping.get("_width", 2)) * 2
    if width <= 0 or len(value) % width:
        width = 4
    chunks = [value[index:index + width] for index in range(0, len(value), width)]
    return "".join(mapping.get(int(chunk, 16), "") for chunk in chunks if chunk)


def _page_text(objects: dict[int, bytes], page_body: bytes) -> str:
    contents_match = re.search(rb"/Contents\s+(\d+)\s+0\s+R", page_body)
    if not contents_match:
        array_match = re.search(rb"/Contents\s*\[(.*?)\]", page_body, re.S)
        if not array_match:
            return ""
        content = b"".join(
            _stream(objects.get(int(ref), b""))
            for ref in re.findall(rb"(\d+)\s+0\s+R", array_match.group(1))
        )
    else:
        content = _stream(objects.get(int(contents_match.group(1)), b""))
    if not content:
        return ""
    text = content.decode("latin-1", errors="replace")
    fonts = _font_maps(objects, page_body)
    current: dict = {}
    pieces: list[str] = []

    token_re = re.compile(
        r"/(F\d+)\s+[\d.]+\s+Tf"
        r"|<([0-9A-Fa-f]+)>\s*Tj"
        r"|\[(.*?)\]\s*TJ"
        r"|\((.*?)\)\s*Tj",
        re.S,
    )
    for token in token_re.finditer(text):
        if token.group(1):
            current = fonts.get(token.group(1), {})
        elif token.group(2):
            pieces.append(_decode_hex_string(token.group(2), current))
        elif token.group(3) is not None:
            for value in re.findall(r"<([0-9A-Fa-f]+)>", token.group(3)):
                pieces.append(_decode_hex_string(value, current))
        elif token.group(4) is not None:
            pieces.append(token.group(4))
    return "".join(pieces)


def read_pdf(path: str | Path) -> PdfReport:
    data = Path(path).read_bytes()
    objects = _objects(data)
    pages = [
        body for body in objects.values()
        if re.search(rb"/Type\s*/Page(?!s)", body)
    ]
    media_boxes = []
    page_texts = []
    for page in pages:
        box = re.search(
            rb"/MediaBox\s*\[\s*([\d.\-]+)\s+([\d.\-]+)\s+([\d.\-]+)\s+([\d.\-]+)\s*\]",
            page,
        )
        if box:
            x1, y1, x2, y2 = (float(value) for value in box.groups())
            media_boxes.append((round(x2 - x1, 3), round(y2 - y1, 3)))
        else:
            media_boxes.append((0.0, 0.0))
        page_texts.append(_page_text(objects, page))
    return PdfReport(len(pages), tuple(media_boxes), tuple(page_texts))
