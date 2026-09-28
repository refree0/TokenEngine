# -*- coding: utf-8 -*-
"""本地 OCR 引擎（PaddleOCR 的 PP-OCR 模型，ONNX 版 RapidOCR）。

用于替代千问 OCR 读取 ST 打印预览等大段英文表格文本：
- 不消耗千问额度、可离线、结果可复现；
- 未安装依赖（onnxruntime / rapidocr_onnxruntime 等）时 available() 返回 False，
  由调用方自动回退到千问视觉 / Windows OCR，不影响主流程。
"""
import os
import re
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

_SIGMA = "\u03a3"   # Σ (Greek capital sigma)
_SUM = "\u2211"     # ∑ (n-ary summation)

_engine = None
_enabled = None     # None => 用配置文件/环境变量默认值


def _config_dir():
    """与 vision_ocr 一致：frozen 用 exe 同级 config 或 _internal/config；开发用项目根 config。"""
    if getattr(sys, "frozen", False):
        root = os.path.dirname(sys.executable)
        cands = [os.path.join(root, "config"), os.path.join(root, "_internal", "config")]
    else:
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        cands = [os.path.join(root, "config")]
    for d in cands:
        if os.path.isdir(d):
            return d
    return cands[0] if cands else None


def _load_enabled():
    p = _config_dir()
    if not p:
        return True
    f = os.path.join(p, "local_ocr_config.txt")
    try:
        if os.path.isfile(f):
            with open(f, encoding="utf-8-sig") as fh:
                for ln in fh:
                    ln = ln.strip()
                    if "=" in ln and not ln.startswith("#"):
                        k, v = ln.split("=", 1)
                        if k.strip() == "enabled":
                            return v.strip().lower() in ("1", "true", "yes", "on")
    except Exception:
        pass
    return True


def set_enabled(v):
    """由 GUI 在启动任务前设置开关（进程内共享）。"""
    global _enabled
    _enabled = bool(v)


def enabled():
    if _enabled is not None:
        return _enabled
    env = os.environ.get("ST_LOCAL_OCR", "").strip().lower()
    if env in ("0", "false", "no", "off"):
        return False
    if env in ("1", "true", "yes", "on"):
        return True
    return _load_enabled()


def available():
    """本地 OCR 依赖是否已安装（不含重依赖，仅探测 rapidocr_onnxruntime）。"""
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


def normalize(text):
    """把本地 OCR 的怪癖归一化到现有报告解析器期望的形态。"""
    if not text:
        return text
    t = text.replace(_SUM, _SIGMA)
    # ΣL 短语：RapidOCR 常把 Σ 认成 Z/E/M，且会粘连 "L is" 成 "Lis"
    t = re.sub(r"(?<=\bof)[ \t]*[ZEM]\s*L\s*is\b", " " + _SIGMA + " L is", t)
    t = re.sub(r"(?<![A-Za-z])[ZEM]\s*L\s*is\b", _SIGMA + " L is", t)
    # strength: 补空格
    t = re.sub(r"\bstrength:\s*", "strength: ", t)
    # 方向词粘连/大小写归一
    t = re.sub(r"\bSTRONGLY\s*UP\b", "STRONGLY UP", t, flags=re.I)
    t = re.sub(r"\bSTRONGLY\s*DOWN\b", "STRONGLY DOWN", t, flags=re.I)
    t = re.sub(r"\bslightly\s*UP\b", "slightly UP", t, flags=re.I)
    t = re.sub(r"\bslightly\s*DOWN\b", "slightly DOWN", t, flags=re.I)
    t = t.replace("DoWN", "DOWN").replace("dOWN", "DOWN")
    return t


def _sort_lines(result):
    items = []
    for r in (result or []):
        box, text = r[0], r[1]
        ys = [p[1] for p in box]
        xs = [p[0] for p in box]
        items.append((min(ys), min(xs), text))
    items.sort(key=lambda t: (round(t[0] / 12), t[1]))
    return [t[2] for t in items]


def ocr_image(image_path):
    """对单张截图做本地 OCR，返回归一化文本；不可用或失败返回 None。"""
    if not (enabled() and available()):
        return None
    try:
        from PIL import Image
        import numpy as np
        im = Image.open(image_path).convert("RGB")
        arr = np.array(im)
        engine = _get_engine()
        result, _ = engine(arr, use_cls=False)
        text = "\n".join(_sort_lines(result))
        return normalize(text)
    except Exception:
        return None
