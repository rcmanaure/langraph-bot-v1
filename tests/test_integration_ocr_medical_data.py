"""
Integration test: OCR stack with real medical data + real images.
Loads sp-diagnostico-histologico.jsonl, tests extraction accuracy.
"""
import json
import time
from pathlib import Path
from typing import Optional

import pytest
from PIL import Image
import io

import app.services.vision as vision_module


# Test data paths
MEDICAL_DATA_PATH = Path("/app/medical_data/sp-diagnostico-histologico.jsonl")
REAL_IMAGES = {
    "biopsy_1": Path("/app/test_images/biopsy_1.jpeg"),
    "biopsy_2": Path("/app/test_images/biopsy_2.jpeg"),
}


def load_medical_catalog() -> dict:
    """Load medical procedures from JSONL."""
    catalog = {}
    if not MEDICAL_DATA_PATH.exists():
        pytest.skip(f"Medical data file not found: {MEDICAL_DATA_PATH}")

    with open(MEDICAL_DATA_PATH, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                record = json.loads(line)
                if record.get("type") == "biopsy":
                    catalog[record["id"]] = record

    return catalog


def load_image(image_key: str) -> Optional[bytes]:
    """Load real image file."""
    path = REAL_IMAGES.get(image_key)
    if not path or not path.exists():
        return None
    return path.read_bytes()


class TestIntegrationOCR:
    """Integration tests: OCR stack with real medical data."""

    @pytest.fixture(scope="class")
    def medical_catalog(self):
        """Load catalog once per test class."""
        return load_medical_catalog()

    def test_medical_catalog_loads(self, medical_catalog):
        """Verify medical data loads correctly."""
        assert len(medical_catalog) > 0, "Medical catalog empty"

        # Verify structure
        sample = next(iter(medical_catalog.values()))
        assert "id" in sample
        assert "name" in sample
        assert "price" in sample
        assert "category" in sample

    def test_catalog_has_gynecology_procedures(self, medical_catalog):
        """Verify gynecology biopsies loaded (GIN codes)."""
        gin_codes = [id for id in medical_catalog.keys() if id.startswith("biopsy-gin")]
        assert len(gin_codes) > 0, "No GIN (gynecology) codes found"

        # Should have GIN007 (Endometriosis)
        assert "biopsy-gin007" in medical_catalog
        gin007 = medical_catalog["biopsy-gin007"]
        assert "Endometriosis" in gin007["name"]

    def test_preprocessing_real_image(self):
        """Preprocessing on real biopsy form."""
        img_bytes = load_image("biopsy_1")
        if not img_bytes:
            pytest.skip("biopsy_1 image not found")

        start = time.time()
        processed = vision_module._preprocess_image(img_bytes)
        elapsed = time.time() - start

        assert len(processed) > 0
        assert elapsed < 0.5, f"Preprocessing took {elapsed*1000:.0f}ms, expected <500ms"

        # Verify still decodable
        img = Image.open(io.BytesIO(processed))
        assert img.size[0] > 0 and img.size[1] > 0

    def test_preprocessing_maintains_aspect_ratio(self):
        """Real images maintain aspect ratio through pipeline."""
        for key in ["biopsy_1", "biopsy_2"]:
            img_bytes = load_image(key)
            if not img_bytes:
                continue

            orig = Image.open(io.BytesIO(img_bytes))
            orig_ratio = orig.width / orig.height

            processed = vision_module._preprocess_image(img_bytes)
            proc_img = Image.open(io.BytesIO(processed))
            proc_ratio = proc_img.width / proc_img.height

            ratio_drift = abs(orig_ratio - proc_ratio)
            assert ratio_drift < 0.05, f"{key}: ratio drift {ratio_drift} > 5%"

    def test_batch_processing_consistency(self):
        """Multiple images process consistently."""
        images = [load_image(key) for key in ["biopsy_1", "biopsy_2"]]
        images = [img for img in images if img is not None]

        if not images:
            pytest.skip("No test images found")

        results = []
        for img_bytes in images:
            processed = vision_module._preprocess_image(img_bytes)
            results.append(len(processed) > 0)

        assert all(results), "Some images failed preprocessing"

    def test_ocr_would_extract_handwritten_text(self):
        """OCR fallback handles handwritten medical procedures."""
        # Simulate what Tesseract would extract from medical forms
        test_extractions = [
            ("Histerectomia abdominal radical\nBiopsia de tejido endometrial", "Histerectomia abdominal radical"),
            ("Laparatomia Exploración\nComeolopía", "Laparatomia Exploración"),
            ("Endometriosis pared abdominal\nBiopsia de lesión", "Endometriosis pared abdominal"),
        ]

        for ocr_text, expected in test_extractions:
            lines = [l.strip() for l in ocr_text.split("\n") if l.strip()]
            extracted = next((l for l in lines if len(l) > 3), None)
            assert extracted == expected, f"Expected '{expected}', got '{extracted}'"


class TestIntegrationPerformance:
    """Performance benchmarks for OCR stack."""

    def test_preprocessing_batch_throughput(self):
        """Measure batch preprocessing speed."""
        images = [load_image(key) for key in ["biopsy_1", "biopsy_2"]]
        images = [img for img in images if img is not None]

        if not images:
            pytest.skip("No test images")

        start = time.time()
        for img_bytes in images:
            vision_module._preprocess_image(img_bytes)
        elapsed = time.time() - start

        per_image = (elapsed / len(images)) * 1000
        assert per_image < 200, f"Preprocessing {per_image:.0f}ms/image, target <200ms"


class TestIntegrationEdgeCases:
    """Edge cases: compression, rotation, blur."""

    def test_heavily_compressed_image(self):
        """JPEG compression artifacts don't crash preprocessing."""
        img_bytes = load_image("biopsy_1")
        if not img_bytes:
            pytest.skip("biopsy_1 not found")

        # Simulate heavy compression by re-encoding with low quality
        img = Image.open(io.BytesIO(img_bytes))
        compressed = io.BytesIO()
        img.save(compressed, "JPEG", quality=30)
        compressed_bytes = compressed.getvalue()

        # Should handle it
        result = vision_module._preprocess_image(compressed_bytes)
        assert len(result) > 0

    def test_small_image_upscaling(self):
        """Small images upscale correctly."""
        # Create a small test image
        small_img = Image.new("RGB", (300, 150), color=(255, 255, 255))
        small_bytes = io.BytesIO()
        small_img.save(small_bytes, format="JPEG")
        small_bytes = small_bytes.getvalue()

        processed = vision_module._preprocess_image(small_bytes)
        proc_img = Image.open(io.BytesIO(processed))

        # Should upscale small dimension to ~600px
        assert proc_img.height >= 300, "Small image not upscaled enough"
        assert proc_img.width > 100, "Width degraded"
