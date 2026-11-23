"""稳定页面自动采集：亮度/清晰度筛选、跨重启近似去重、原图落盘。"""
from datetime import datetime, timezone
import json
from pathlib import Path
import queue
import threading
import time
import uuid
import cv2
import numpy as np
from shared.records import atomic_json


def signature(image):
    small = cv2.resize(cv2.cvtColor(image, cv2.COLOR_BGR2GRAY), (33, 32))
    return np.packbits(small[:, 1:] > small[:, :-1]).tobytes().hex()


def signature_distance(left, right):
    return bin(int(left, 16) ^ int(right, 16)).count('1')


def comparison_thumbnail(image):
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    return cv2.resize(gray, (320, 180), interpolation=cv2.INTER_AREA).astype(np.float32)


class StablePageDetector:
    def __init__(self, known=(), stable_seconds=2.0, min_sharpness=60, change_threshold=4.0):
        self.known = list(known)
        self.references = {}
        self.stable_seconds = stable_seconds
        self.min_sharpness = min_sharpness
        self.change_threshold = change_threshold
        self.windows = {size: cv2.createHanningWindow(size, cv2.CV_32F)
                        for size in ((160, 90), (320, 180))}
        self.anchor = None
        self.candidate = None
        self.interrupted_at = None
        self.latest = None
        self.accepted_frame = None
        self.since = None
        self.note = '等待稳定清晰页面'

    def observe(self, image, now):
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        small = cv2.resize(gray, (160, 90), interpolation=cv2.INTER_AREA).astype(np.float32)
        brightness = float(gray.mean())
        sharpness = float(cv2.Laplacian(cv2.resize(gray, (640, 360)), cv2.CV_64F).var())
        metrics = dict(brightness=brightness, sharpness=sharpness)
        if brightness < 15 or sharpness < self.min_sharpness:
            self.candidate = None
            if self.interrupted_at is None:
                self.interrupted_at = now
            elif now-self.interrupted_at >= .35:
                self.anchor, self.since = None, None
            if brightness < 15:
                self.note = '跳过：画面过暗，请补光或开启曝光补偿'
            elif brightness > 245:
                self.note = '跳过：画面过亮，请降低亮度'
            else:
                self.note = '跳过：画面不够清晰，请对焦并让 PPT 占满画面'
            return None
        if self.anchor is None:
            self.anchor, self.since = small, now
        elif not self.same_view(small, self.anchor):
            if self.interrupted_at is None:
                self.interrupted_at = now
            if self.candidate is None or not self.same_view(small, self.candidate[0]):
                self.candidate = small, now
            candidate, started = self.candidate
            if now-started < .35:
                self.note = '画面有变化，正在确认新页面'
                return None
            self.anchor, self.since = candidate, started
        elif self.interrupted_at is not None:
            # 短暂晃动或模糊只暂停稳定计时，持续变化仍需确认新页面。
            self.since += now-self.interrupted_at
        self.candidate = self.interrupted_at = None
        self.latest = small
        remaining = self.stable_seconds-(now-self.since)
        if remaining > 0:
            self.note = '正在确认页面稳定 · 还需 {:.1f} 秒'.format(remaining)
            return None
        # 同一个稳定画面只保存一次，避免传感器噪声改变细节指纹时连续写盘。
        if self.accepted_frame is not None and self.same_view(small, self.accepted_frame, duplicate=True):
            self.note = '已采集此页，跳过重复'
            return None
        fingerprint = signature(image)
        if self.is_duplicate(fingerprint, image):
            self.accepted_frame = self.latest
            self.note = '已采集此页，跳过重复'
            return None
        self.note = '页面稳定，保存原图'
        return dict(metrics, signature=fingerprint)

    def same_view(self, image, reference, duplicate=False):
        height, width = image.shape
        scale = width//160
        archive = duplicate and scale == 2
        grid = (9, 20, 16, 20) if archive else (6, 15*scale, 8, 20*scale)
        mean_limit = min(2.0, self.change_threshold) if duplicate else self.change_threshold
        fraction_limit = .01 if duplicate and not archive else .02
        block_limit = .18 if archive else (.15 if duplicate else .23)

        def similar(frame, aligned=False):
            delta = cv2.GaussianBlur(frame-reference, (3, 3), 0)
            # 消除整行明暗波动，再检查文字结构是否变化。
            difference = np.abs(delta-np.median(delta, axis=1, keepdims=True))
            if aligned:
                difference[:5*scale, :] = difference[-5*scale:, :] = 0
                difference[:, :6*scale] = difference[:, -6*scale:] = 0
            significant = difference > 12
            blocks = significant.reshape(grid).mean(axis=(1, 3))
            return (difference.mean() <= mean_limit and significant.mean() <= fraction_limit
                    and blocks.max() <= block_limit)

        if similar(image):
            return True
        # 先尝试平移对齐，再校正小幅旋转。大幅移动或换页仍需重新计时。
        image_edges = image-cv2.GaussianBlur(image, (0, 0), 2)
        reference_edges = reference-cv2.GaussianBlur(reference, (0, 0), 2)
        (dx, dy), confidence = cv2.phaseCorrelate(reference_edges, image_edges, self.windows[(width, height)])
        if not (confidence >= .3 and abs(dx) <= 5*scale and abs(dy) <= 4*scale):
            return False
        transform = np.float32([[1, 0, dx], [0, 1, dy]])

        def matches_transform(matrix):
            aligned = cv2.warpAffine(image, matrix, (width, height),
                                     flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP,
                                     borderMode=cv2.BORDER_REPLICATE)
            return similar(aligned, aligned=True)

        if matches_transform(transform):
            return True
        if confidence < .5:
            return False
        criteria = (cv2.TERM_CRITERIA_COUNT | cv2.TERM_CRITERIA_EPS, 15, .0005)
        try:
            score, transform = cv2.findTransformECC(reference_edges, image_edges, transform,
                                                    cv2.MOTION_EUCLIDEAN, criteria, None, 3)
        except cv2.error:
            return False
        center = np.float32([width/2, height/2])
        offset = transform[:, :2] @ center + transform[:, 2] - center
        return (score >= .8 and abs(transform[0, 1]) <= .021
                and abs(offset[0]) <= 5*scale and abs(offset[1]) <= 4*scale
                and matches_transform(transform))

    def is_duplicate(self, fingerprint, image):
        thumbnail = None
        for prior in self.known:
            references = self.references.get(prior)
            if not references:
                if signature_distance(fingerprint, prior) <= 12:
                    return True
                continue
            if thumbnail is None:
                thumbnail = comparison_thumbnail(image)
            for index, reference in enumerate(references):
                if isinstance(reference, Path):
                    pixels = cv2.imread(str(reference))
                    if pixels is None:
                        continue
                    reference = references[index] = comparison_thumbnail(pixels)
                # 内容只平移一点也会改变感知哈希，对已有原图用对齐后的局部差异去重。
                if self.same_view(thumbnail, reference, duplicate=True):
                    return True
        return False

    def accepted(self, fingerprint, reference=None):
        # 仅成功写盘后加入去重库，磁盘错误不应丢失采集机会。
        self.accepted_frame = self.latest
        if fingerprint not in self.known:
            self.known.append(fingerprint)
        if reference is not None:
            self.references.setdefault(fingerprint, []).append(reference)


class AutoCollector:
    def __init__(self, folder, device=None):
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        self.frames = queue.Queue(maxsize=1)
        self.events = queue.Queue()
        self.stop_event = threading.Event()
        self.device = device
        self.detector = StablePageDetector()
        for page in self.list_pages():
            self.detector.accepted(page['signature'], self.folder/page['image'])
        self.note = '等待摄像头画面'
        self.saved_count = 0
        self.thread = threading.Thread(target=self._work, name='ppt-auto-capture', daemon=True)
        self.thread.start()

    def list_pages(self):
        pages = []
        for path in self.folder.glob('*.json'):
            value = json.loads(path.read_text(encoding='utf-8'))
            if not value.get('deleted_at') and (self.folder/value['image']).is_file():
                pages.append(value)
        return sorted(pages, key=lambda r: r['captured_at'])

    def submit(self, image):
        if self.stop_event.is_set():
            return
        try:
            self.frames.put_nowait((time.monotonic(), image.copy()))
        except queue.Full:
            pass

    def _work(self):
        while True:
            try:
                captured, image = self.frames.get(timeout=0.2)
            except queue.Empty:
                if self.stop_event.is_set():
                    return
                continue
            try:
                result = self.detector.observe(image, captured)
                self.note = self.detector.note
                if result is None:
                    continue
                identifier = uuid.uuid4().hex
                destination = self.folder/(identifier+'.png')
                okay, encoded = cv2.imencode('.png', image)
                if not okay:
                    raise ValueError('自动采集图片编码失败')
                destination.write_bytes(encoded.tobytes())
                result.update(id=identifier, image=destination.name,
                              captured_at=datetime.now(timezone.utc).isoformat(), state='等待OCR',
                              device=self.device)
                atomic_json(self.folder/(identifier+'.json'), result)
                self.saved_count += 1
                self.detector.accepted(result['signature'], comparison_thumbnail(image))
                self.events.put(result)
                self.note = '已自动采集，可在采集相册中打开识别'
            except Exception as exc:
                self.note = '自动采集失败：'+str(exc)

    def close(self):
        self.stop_event.set()
