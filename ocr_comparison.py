"""保存同一输入的校正前后NPU OCR、检测图与操作参数，支持离线复查。"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import uuid
import cv2
import numpy as np
from records import atomic_json


def image_digest(image):
    return hashlib.sha256(str(image.shape).encode()+str(image.dtype).encode()+image.tobytes()).hexdigest()


def normalize(text):
    return re.sub(r'[^\w\u4e00-\u9fff]', '', text).lower()


def character_error_rate(reference, actual):
    reference, actual = normalize(reference), normalize(actual)
    if not reference:
        raise ValueError('请先输入非空的人工核对原文')
    row = list(range(len(actual)+1))
    for index, expected in enumerate(reference, 1):
        following = [index]
        for column, found in enumerate(actual, 1):
            following.append(min(following[-1]+1, row[column]+1, row[column-1]+(expected != found)))
        row = following
    return row[-1]/len(reference)


def find_comparison(folder, original, corrected=None):
    left, right = image_digest(original), image_digest(corrected) if corrected is not None else None
    matches = []
    for path in Path(folder).glob('*/comparison.json'):
        result = json.loads(path.read_text(encoding='utf-8'))
        if result['original_digest'] == left and (right is None or result['corrected_digest'] == right):
            matches.append(result)
    return max(matches, key=lambda r: r['created_at']) if matches else None


def compare(engine, original, corrected, folder, operations=()):
    if image_digest(original) == image_digest(corrected):
        raise ValueError('图片尚未改变，请先选择四角并执行区域校正')
    identifier = uuid.uuid4().hex
    destination = Path(folder)/identifier
    destination.mkdir(parents=True)
    result = dict(id=identifier, created_at=datetime.now(timezone.utc).isoformat(),
                  original_digest=image_digest(original), corrected_digest=image_digest(corrected),
                  operations=list(operations), backend='PP-OCRv4 / RK3588 NPU',
                  accuracy_note='字符数、置信度和清晰度不代表准确率；CER须提供人工参考原文')
    for label, image in [('original', original), ('corrected', corrected)]:
        recognized, annotated = engine.recognize(image)
        for name, pixels in [(label+'.png', image), (label+'_boxes.png', annotated)]:
            if not cv2.imwrite(str(destination/name), pixels):
                raise OSError('对照图片写入失败')
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        gray = cv2.resize(gray, (640, max(1, round(gray.shape[0]*640/gray.shape[1]))))
        result[label] = dict(ocr=recognized, image=label+'.png', annotated=label+'_boxes.png',
                             shape=list(image.shape), sharpness=float(cv2.Laplacian(gray, cv2.CV_64F).var()))
    atomic_json(destination/'comparison.json', result)
    return result


def save_reference(folder, result, reference):
    if len(reference) > 10000:
        raise ValueError('人工参考最多10000字符，请按单页核对')
    metrics = dict(reference=reference, normalization='letters/digits/Chinese only; lowercase; Unicode word characters',
                   original_cer=character_error_rate(reference, result['original']['ocr']['text']),
                   corrected_cer=character_error_rate(reference, result['corrected']['ocr']['text']),
                   reference_kind='user-entered transcription; not independently verified')
    atomic_json(Path(folder)/result['id']/'reference.json', metrics)
    return metrics


def export_comparison(result, destination, reference=None):
    lines = ['同图OCR对照', '对照编号：'+result['id'], '后端：'+result['backend'], result['accuracy_note']]
    for key, title in [('original', '校正前'), ('corrected', '校正后')]:
        ocr = result[key]['ocr']
        lines += ['', title+' · {:.3f}秒 · {}行'.format(ocr['elapsed_seconds'], len(ocr['lines'])), ocr['text']]
    if reference:
        lines += ['', '人工参考（未独立复核）：', reference['reference'],
                  '归一化CER：校正前{:.2%}；校正后{:.2%}'.format(reference['original_cer'], reference['corrected_cer']),
                  '归一化方法：'+reference['normalization']]
    Path(destination).write_text('\n'.join(lines)+'\n', encoding='utf-8')
