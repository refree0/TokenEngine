# -*- coding: utf-8 -*-
"""TokenEngine 本地 OCR 引擎（RapidOCR / PP-OCR ONNX，纯 CPU、完全离线）。

视觉云源全部耗尽时由 router 调用做最终兜底：
- 不消耗任何云额度、可离线、结果可复现；
- 仅返回图中文字（OCR），不做语义理解；
- 依赖未安装时 available() 返回 False，router 跳过本兜底。
"""
import base64
import io

_engine = None


def available():
    try:
        import rapidocr_onnxruntime  # noqa: F401
        return True
    except Exception:
        return False


def _get_engine():
    global _engine
    if _engine is None:
        from rapidocr_onnxruntime import RapidOCR
        _engine = RapidOCR()
    return _engine


def _sort_lines(result):
    """按从上到下、从左到右还原阅读顺序。"""
    items = []
    for r in (result or []):
        box, text = r[0], r[1]
        ys = [p[1] for p in box]
        xs = [p[0] for p in box]
        items.append((min(ys), min(xs), text))
    items.sort(key=lambda t: (round(t[0] / 12), t[1]))
    return [t[2] for t in items]


def ocr_data_url(data_url):
    """识别 data:image/...;base64,xxx 或纯 base64 图片，返回多行文本；失败返回 None。"""
    try:
        if isinstance(data_url, str) and data_url.startswith("data:") and "," in data_url:
            b64 = data_url.split(",", 1)[1]
        else:
            b64 = data_url
        raw = base64.b64decode(b64)
        from PIL import Image
        import numpy as np
        im = Image.open(io.BytesIO(raw)).convert("RGB")
        arr = np.array(im)
        result, _ = _get_engine()(arr, use_cls=False)
        lines = _sort_lines(result)
        return "\n".join(lines) if lines else ""
    except Exception:
        return None
