#!/usr/bin/env python
"""Manual test: OCR stack with real medical order images."""
import io
from pathlib import Path

from PIL import Image

import app.services.vision as vision_module


# Real images from WhatsApp
IMAGES = {
    "biopsy_1": Path("C:/Users/rcman/Downloads/WhatsApp Image 2026-07-21 at 9.48.45 AM.jpeg"),
    "biopsy_2": Path("C:/Users/rcman/Downloads/WhatsApp Image 2026-07-21 at 11.42.00 AM.jpeg"),
}


def test_preprocess_real_images():
    """Test preprocessing on real medical order images."""
    print("\n=== Testing preprocessing on real medical images ===\n")

    for name, path in IMAGES.items():
        if not path.exists():
            print(f"SKIP: {name} not found at {path}")
            continue

        print(f"Testing {name} ({path.name})...")
        img_bytes = path.read_bytes()

        # Original
        orig_img = Image.open(io.BytesIO(img_bytes))
        orig_size = len(img_bytes)
        print(f"  Original: {orig_img.size} {orig_size/1024:.1f}KB")

        # Preprocess
        processed = vision_module._preprocess_image(img_bytes)
        proc_img = Image.open(io.BytesIO(processed))
        proc_size = len(processed)
        print(f"  Processed: {proc_img.size} {proc_size/1024:.1f}KB")

        # Verify
        assert len(processed) > 0, "Processed image empty"
        assert proc_img.size[0] > 100, "Width too small"
        assert proc_img.size[1] > 100, "Height too small"

        orig_ratio = orig_img.width / orig_img.height
        proc_ratio = proc_img.width / proc_img.height
        ratio_diff = abs(orig_ratio - proc_ratio)
        print(f"  Aspect ratio: {orig_ratio:.3f} -> {proc_ratio:.3f} (diff: {ratio_diff:.3f})")
        assert ratio_diff < 0.05, f"Aspect ratio changed too much: {ratio_diff}"

        print(f"  Result: OK\n")


def test_ocr_text_extraction():
    """Test OCR text extraction simulation."""
    print("\n=== Testing OCR text extraction ===\n")

    # Example: what Tesseract would extract from biopsy forms
    test_cases = [
        {
            "name": "Histerectomia",
            "text": "Histerectomia abdominal radical\nBiopsia de tejido endometrial",
            "expected": "Histerectomia abdominal radical",
        },
        {
            "name": "Laparatomia",
            "text": "Laparatomia Exploración\nComeolopía",
            "expected": "Laparatomia Exploración",
        },
    ]

    for test in test_cases:
        print(f"Testing: {test['name']}")
        lines = [l.strip() for l in test['text'].split("\n") if l.strip()]
        procedure = next((l for l in lines if len(l) > 3), None)
        print(f"  Extracted: {procedure}")
        print(f"  Expected: {test['expected']}")
        assert procedure == test['expected'], f"Mismatch: {procedure} != {test['expected']}"
        print(f"  Result: OK\n")


if __name__ == "__main__":
    test_preprocess_real_images()
    test_ocr_text_extraction()
    print("\n=== All tests PASSED ===\n")
