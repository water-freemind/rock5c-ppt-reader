"""板端NPU整页OCR。官方后处理随课程包保留许可证。"""
from pathlib import Path
import sys
import time
import json
import ctypes
from unittest.mock import patch
import cv2
import numpy as np
from rknnlite.api import RKNNLite
from board_app.ocr.reading_order import sort_text_boxes
from shared.paths import PROJECT_ROOT
from vendor.ocr_conda_lab.utils.operators import DetResizeForTest
from vendor.ocr_conda_lab.utils.db_postprocess import DBPostProcess
from vendor.ocr_conda_lab.utils.rec_postprocess import CTCLabelDecode

ROOT = PROJECT_ROOT
COURSE = ROOT / 'vendor/ocr_conda_lab'


def order_quad(points):
    points = np.asarray(points, dtype=np.float32)
    indices = np.argsort(points[:, 0])
    left, right = points[indices[:2]], points[indices[2:]]
    left = left[np.argsort(left[:, 1])]
    right = right[np.argsort(right[:, 1])]
    return np.array([left[0], right[0], right[1], left[1]], np.float32)


def crop_line(image, box):
    p = order_quad(box)
    width = max(2, int(max(np.linalg.norm(p[0]-p[1]), np.linalg.norm(p[2]-p[3]))))
    height = max(2, int(max(np.linalg.norm(p[0]-p[3]), np.linalg.norm(p[1]-p[2]))))
    matrix = cv2.getPerspectiveTransform(p, np.float32([[0, 0], [width-1, 0],
                                                      [width-1, height-1], [0, height-1]]))
    result = cv2.warpPerspective(image, matrix, (width, height), borderMode=cv2.BORDER_REPLICATE)
    return cv2.rotate(result, cv2.ROTATE_90_COUNTERCLOCKWISE) if height > width * 1.5 else result


def split_long_line(image):
    """避免长行压缩到320px丢字；优先在投影墨迹最少的列切分。"""
    h, w = image.shape[:2]
    limit = max(32, int(h*6.5))
    if w <= limit:
        return [image]
    # 实拍行的背光和底色不均，固定阈值会把整块暗背景计为墨迹。
    # 局部阈值只用来定位间隙；传给 NPU 的仍是原彩色裁图。
    gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
    block = max(9, h | 1)
    ink = cv2.adaptiveThreshold(gray, 1, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                cv2.THRESH_BINARY_INV, block, 7).sum(axis=0)
    parts = []
    start = 0
    while w-start > limit:
        low = start+int(limit*0.75)
        high = min(w-1, start+limit)
        # 低墨迹优先；同分时靠右切，避免产生过短片段。
        candidates = np.arange(low, high+1)
        seam = int(candidates[np.argmin(ink[candidates]+(high-candidates)*0.001)])
        parts.append(image[:, start:seam])
        start = seam
    parts.append(image[:, start:])
    return parts


class OCREngine:
    def __init__(self):
        self.models = []
        try:
            for mode in ('det', 'rec'):
                model = RKNNLite()
                self.models.append(model)
                if model.load_rknn(str(ROOT / 'models' / ('ppocrv4_' + mode + '_rk3588.rknn'))) != 0:
                    raise RuntimeError(mode + '模型加载失败')
                # Lite在ARM板上使用绝对系统路径。仅初始化期间重定向RKNN库，
                # 不修改site-packages和系统文件；此引擎须在单一工作线程创建。
                original_cdll = ctypes.CDLL
                private_runtime = ROOT / 'librknnrt.so'
                def load_private(name, *args, **kwargs):
                    if str(name).endswith('librknnrt.so'):
                        name = str(private_runtime)
                    return original_cdll(name, *args, **kwargs)
                with patch('rknnlite.api.rknn_runtime.CDLL', side_effect=load_private):
                    if model.init_runtime(core_mask=RKNNLite.NPU_CORE_AUTO) != 0:
                        raise RuntimeError(mode + 'NPU初始化失败')
            self.post = DBPostProcess(thresh=0.3, box_thresh=0.6, unclip_ratio=1.5)
            self.decode = CTCLabelDecode(str(COURSE / 'ppocr_keys_v1.txt'), use_space_char=True)
        except Exception:
            self.close()
            raise

    def close(self):
        for model in self.models:
            model.release()
        self.models = []

    def recognize(self, image, reading_order='rows'):
        if image is None or image.size == 0:
            raise ValueError('图片无法读取')
        started = time.monotonic()
        rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        data = DetResizeForTest(image_shape=[480, 480])({'image': rgb})
        outputs = self.models[0].inference(inputs=[data['image'][None, ...]])
        if outputs is None:
            raise RuntimeError('NPU检测推理失败')
        boxes = self.post({'maps': outputs[0].astype(np.float32)}, data['shape'])[0]['points']
        # 不用轴对齐包围框的高度判断同行：长斜行会虚增高度并吞并下一行。
        boxes = sort_text_boxes(boxes, mode=reading_order)
        lines = []
        annotated = image.copy()
        for box in boxes:
            crop = cv2.cvtColor(crop_line(image, box), cv2.COLOR_BGR2RGB)
            fragments = []
            for part in split_long_line(crop):
                h, w = part.shape[:2]
                scaled_width = min(320, max(1, int(np.ceil(48*w/h))))
                resized = cv2.resize(part, (scaled_width, 48)).astype(np.float32)
                tensor = np.zeros((48, 320, 3), dtype=np.float32)
                tensor[:, :scaled_width, :] = (resized-127.5)/127.5
                prediction = self.models[1].inference(inputs=[tensor[None, ...]])
                if prediction is None:
                    raise RuntimeError('NPU识别推理失败')
                fragments.append(self.decode(prediction[0].astype(np.float32))[0])
            text = ''.join(fragment[0] for fragment in fragments)
            score = sum(float(s)*len(t) for t, s in fragments)/max(1, sum(len(t) for t, s in fragments))
            lines.append(dict(text=text, confidence=float(score), box=np.asarray(box).tolist()))
            cv2.polylines(annotated, [np.asarray(box, np.int32)], True, (0, 200, 0), 2)
        return dict(text='\n'.join(line['text'] for line in lines if line['text']), lines=lines,
                    reading_order=reading_order,
                    elapsed_seconds=time.monotonic()-started,
                    status='ok' if any(line['text'] for line in lines) else '未检测到可识别文字'), annotated


if __name__ == '__main__':
    source = Path(sys.argv[1]) if len(sys.argv) > 1 else COURSE / 'ppt.png'
    evidence = ROOT / 'evidence'
    evidence.mkdir(exist_ok=True)
    engine = OCREngine()
    try:
        result, annotated = engine.recognize(cv2.imread(str(source)))
        print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
        (evidence / (source.stem + '_ocr.json')).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        cv2.imwrite(str(evidence / (source.stem + '_detected.jpg')), annotated)
    finally:
        engine.close()
