# -*- coding: utf-8 -*-
import os
_ROOT = os.path.dirname(os.path.abspath(__file__))
import base64, sys
sys.path.insert(0, _ROOT)
import local_ocr_engine as e

print("available:", e.available())
IMG = __import__("os").environ.get("TOKENENGINE_TEST_IMAGE", "test_image.png")
with open(IMG, "rb") as f:
    du = "data:image/png;base64," + base64.b64encode(f.read()).decode()
txt = e.ocr_data_url(du)
print("=== OCR result ===")
print(txt)
