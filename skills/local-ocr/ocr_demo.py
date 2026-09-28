# -*- coding: utf-8 -*-
"""本地 OCR 最小示例（RapidOCR / PaddleOCR PP-OCR 的 ONNX 版）。

安装：
    pip install rapidocr_onnxruntime

用法：
    python ocr_demo.py <图片路径>
"""
import sys
import time

from rapidocr_onnxruntime import RapidOCR


def ocr(image_path):
    """识别图片文字，返回按阅读顺序排列的文本。"""
    engine = RapidOCR()
    # use_cls=False：关掉文字方向分类，正立文本更快，精度不降
    result, _ = engine(image_path, use_cls=False)
    if not result:
        return ""
    # result: [[box, text, score], ...]，按 y 分组、x 排序还原阅读顺序
    items = []
    for box, text, score in result:
        xs = [p[0] for p in box]
        ys = [p[1] for p in box]
        items.append((min(ys), min(xs), text))
    items.sort(key=lambda t: (round(t[0] / 12), t[1]))
    return "\n".join(t[2] for t in items)


def main():
    if len(sys.argv) < 2:
        print("用法: python ocr_demo.py <图片路径>")
        sys.exit(1)
    t0 = time.time()
    text = ocr(sys.argv[1])
    print("耗时 %.2f 秒，%d 字符" % (time.time() - t0, len(text)))
    print("=" * 50)
    print(text)


if __name__ == "__main__":
    main()
