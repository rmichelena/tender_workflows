"""Tests para validación bbox, OCR zeros y análisis JSON en pdf_plan_pages."""

from __future__ import annotations

import json
from pathlib import Path

import fitz
import pdf_plan_pages as pp


def test_parse_bbox_pct_rejects_full_page_and_invalid():
    assert pp.parse_bbox_pct([0, 0, 1, 1]) is None
    assert pp.parse_bbox_pct([0, 0, 0.5, 0.5]) is not None
    assert pp.parse_bbox_pct("bad") is None
    assert pp.parse_bbox_pct([0, 0, 1.5, 0.2]) is None
    assert pp.parse_bbox_pct([0.2, 0.2, 0.205, 0.205]) is None  # dimensión < 1%


def test_normalize_zeros_for_ocr_adds_variation_selector():
    out = pp.normalize_zeros_for_ocr("CO-001 B0")
    assert out.count("\uFE00") == 3
    assert "O" in out


def test_unicode_font_path_uses_bundled_noto():
    path = pp.unicode_font_path()
    assert path.name == "NotoSans-Regular.ttf"
    assert path.exists()


def test_load_analysis_requires_replace_images_entries(tmp_path: Path):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"pages": [{"page": 1, "action": "replace_images"}]}), encoding="utf-8")
    try:
        pp.load_analysis(bad)
        raise AssertionError("expected SystemExit")
    except SystemExit as exc:
        assert "image_replacements" in str(exc)


def test_load_analysis_accepts_minimal_valid(tmp_path: Path):
    good = tmp_path / "good.json"
    good.write_text(
        json.dumps(
            {
                "pages": [
                    {
                        "page": 1,
                        "action": "leave_for_ocr",
                        "identifier_or_title": None,
                        "visual_type": "text",
                        "summary": "",
                        "technical_observations": [],
                        "visible_text_or_codes": [],
                        "limitations": [],
                        "confidence": "high",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    data = pp.load_analysis(good)
    assert len(data["pages"]) == 1


def test_plan_replacement_rect_skips_invalid_bbox():
    doc = fitz.open()
    page = doc.new_page(width=600, height=800)
    rect, reason = pp.plan_replacement_rect(page, [0, 0, 1, 1])
    assert rect is None
    assert reason == "invalid_bbox_pct"
    doc.close()


def test_plan_replacement_rect_accepts_small_bbox_without_image_blocks():
    doc = fitz.open()
    page = doc.new_page(width=600, height=800)
    rect, reason = pp.plan_replacement_rect(page, [0.1, 0.1, 0.4, 0.35])
    assert reason is None
    assert rect is not None
    assert pp.rect_area_ratio(rect, page.rect) <= pp.MAX_UNMATCHED_AREA_RATIO
    doc.close()


def test_make_replacement_image_stream_returns_png():
    png = pp.make_replacement_image_stream(200, 120, "Prueba 25°C CO-001")
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
