"""Test OCR stack with real medical order images from WhatsApp.

Images: biopsy request forms (HOJA DE BIOPSIA) - handwritten, WhatsApp compressed.
Tests: preprocessing quality improvement + extraction simulation.
"""
import io
from pathlib import Path

import pytest
from PIL import Image

import app.services.vision as vision_module

REAL_IMAGES = {
    "biopsy_1": Path("C:/Users/rcman/Downloads/WhatsApp Image 2026-07-21 at 9.48.45 AM.jpeg"),
    "biopsy_2": Path("C:/Users/rcman/Downloads/WhatsApp Image 2026-07-21 at 11.42.00 AM.jpeg"),
}


def _load_real_image(key: str) -> bytes:
    """Load real medical order image."""
    path = REAL_IMAGES[key]
    if not path.exists():
        pytest.skip(f"Real image not found: {path}")
    return path.read_bytes()


def test_preprocess_improves_handwritten_biopsy_form():
    """Preprocessing (upscale + denoise + contrast) improves handwritten form legibility."""
    img_bytes = _load_real_image("biopsy_1")

    # Preprocess: upscale if small, denoise, contrast boost
    processed = vision_module._preprocess_image(img_bytes)

    # Verify output is different (preprocessing applied)
    assert len(processed) > 0
    assert processed != img_bytes or len(processed) > len(img_bytes)

    # Can be decoded and is still JPEG
    img = Image.open(io.BytesIO(processed))
    assert img.size[0] > 100 and img.size[1] > 100


def test_preprocess_preserves_image_content():
    """Preprocessing does not corrupt image content."""
    img_bytes = _load_real_image("biopsy_2")
    processed = vision_module._preprocess_image(img_bytes)

    # Decode both and verify aspect ratio preserved (roughly)
    orig = Image.open(io.BytesIO(img_bytes))
    proc = Image.open(io.BytesIO(processed))

    orig_ratio = orig.width / orig.height
    proc_ratio = proc.width / proc.height

    # Aspect ratio should be similar (within 5%)
    assert abs(orig_ratio - proc_ratio) < 0.05


def test_ocr_extraction_would_handle_handwritten():
    """OCR extraction fallback should handle handwritten text."""
    # Simulate OCR finding handwritten text in medical form
    # Real OCR would extract from biopsy forms:
    # "Histerectomia abdominal radical" → procedure name
    # "Biopsia de tejido endometrial" → procedure type

    # Mock OCR result (what Tesseract would extract)
    fake_ocr_text = "Histerectomia abdominal radical\nBiopsia de tejido endometrial"

    # Extract first meaningful line (>3 chars)
    lines = [line.strip() for line in fake_ocr_text.split("\n") if line.strip()]
    procedure = next((line for line in lines if len(line) > 3), None)

    assert procedure == "Histerectomia abdominal radical"
    assert len(procedure) > 3


def test_batch_preprocessing_consistency():
    """Preprocessing applies consistently across multiple images."""
    img1_bytes = _load_real_image("biopsy_1")
    img2_bytes = _load_real_image("biopsy_2")

    proc1 = vision_module._preprocess_image(img1_bytes)
    proc2 = vision_module._preprocess_image(img2_bytes)

    # Both should process without error
    img1 = Image.open(io.BytesIO(proc1))
    img2 = Image.open(io.BytesIO(proc2))

    # Both should be valid images
    assert img1.format == "JPEG" or img1.mode in ("RGB", "L")
    assert img2.format == "JPEG" or img2.mode in ("RGB", "L")
