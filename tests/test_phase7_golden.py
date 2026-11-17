"""T-DOC-007 golden reference for the owner-approved V1 quotation template.

The reference is a deterministic content/layout manifest rather than a pixel
image.  Arabic text, digits, and decimal separators are rendered by the
approved bundled Noto Naskh Arabic font, but Latin tokens (``EGP``, document
codes, dashes, parentheses) are absent from that font and fall back to a
Windows system font (Tahoma).  A pixel comparison would therefore depend on an
unapproved system font, so the golden locks the page plan, page geometry, and
canonical text instead; glyph ownership is asserted separately below.
"""

from __future__ import annotations

import json
from pathlib import Path

from mizan.rendering import (
    A4QuotationRenderer,
    RENDERER_VERSION,
    TEMPLATE_VERSION,
)
from pdf_probe import font_text_map
from phase7_golden_support import build_golden_manifest, build_review_snapshot

GOLDEN = Path(__file__).parent / "golden" / "quotation-a4-v1.json"


def _render(tmp_path, name: str = "golden.pdf"):
    renderer = A4QuotationRenderer()
    snapshot = build_review_snapshot()
    pdf = tmp_path / name
    result = renderer.render_pdf(snapshot, pdf)
    return renderer, snapshot, pdf, result


def test_approved_template_matches_golden_reference(tmp_path):
    renderer, snapshot, pdf, result = _render(tmp_path)
    manifest = build_golden_manifest(renderer, snapshot, pdf)
    expected = json.loads(GOLDEN.read_text(encoding="utf-8"))

    assert manifest == expected
    assert result.pages == expected["page_count"] == 3
    assert [len(page["line_numbers"]) for page in expected["pages"]] == [20, 20, 5]
    assert [page["has_summary"] for page in expected["pages"]] == [
        False, False, True,
    ]
    assert (expected["template_version"], expected["renderer_version"]) == (
        TEMPLATE_VERSION,
        RENDERER_VERSION,
    )


def test_golden_reference_is_deterministic(tmp_path):
    renderer = A4QuotationRenderer()
    snapshot = build_review_snapshot()
    first = tmp_path / "first.pdf"
    second = tmp_path / "second.pdf"
    renderer.render_pdf(snapshot, first)
    renderer.render_pdf(snapshot, second)

    assert build_golden_manifest(renderer, snapshot, first) == build_golden_manifest(
        renderer, snapshot, second
    )


def test_money_glyphs_come_from_the_approved_bundled_font(tmp_path):
    _renderer, _snapshot, pdf, _result = _render(tmp_path, "font-split.pdf")
    fonts = font_text_map(pdf)
    approved = [name for name in fonts if name.startswith("NotoNaskhArabic")]
    assert approved, sorted(fonts)
    approved_text = "".join(fonts[name] for name in approved)

    # Every money character, including the decimal separator, is shaped by the
    # approved font; only Latin letters/punctuation rely on the fallback.
    assert all(character in approved_text for character in "0123456789.")
    assert "1,281.41" in approved_text
    assert "EGP" not in approved_text
