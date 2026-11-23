"""Keep raw OCR evidence and export a saved page with its matching summary."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import uuid
import zipfile

import cv2

from ocr_comparison import image_digest
from records import RecordStore, atomic_json


def save_ocr_evidence(root, original, image, annotated, result, operations):
    identifier = uuid.uuid4().hex
    folder = Path(root)/'ocr-evidence'/identifier
    folder.mkdir(parents=True)
    for name, pixels in [('ocr_input.png', image), ('detection.png', annotated)]:
        if not cv2.imwrite(str(folder/name), pixels):
            raise OSError('无法写入OCR验收图')
    atomic_json(folder/'ocr.json', dict(
        original_pixel_digest=image_digest(original),
        recognized_at=datetime.now(timezone.utc).isoformat(),
        backend='PP-OCRv4 / RK3588 NPU',
        operations=operations,
        result=result,
    ))
    return identifier


def export_page_evidence(store, record, destination):
    """Export only the selected summary and its own raw recognition evidence."""
    summary = record['summary']
    identifier = summary.get('ocr_evidence_id', '')
    original = store.image_path(record)
    found = {}
    if identifier:
        if not re.fullmatch('[a-f0-9]{32}', identifier):
            raise ValueError('OCR证据编号不合法')
        folder = store.root/'ocr-evidence'/identifier
        found = {name: folder/name for name in ('ocr.json', 'ocr_input.png', 'detection.png')}
        evidence = json.loads(found['ocr.json'].read_text(encoding='utf-8'))
        pixels = cv2.imread(str(original))
        if pixels is None or evidence['original_pixel_digest'] != image_digest(pixels):
            raise ValueError('检测证据与原图不匹配')
        raw_text = evidence['result']['text']
    notes = [
        '页面编号：'+record['record_id'],
        '记录时间：'+record['captured_at'],
        '摘要来源：'+RecordStore.source_label(summary),
        ('保存了摘要所用页面的原始检测结果，请分别核对机器原文和摘要输入。' if found else
         '该历史摘要没有关联原始检测记录。重新识别可补做测试，请独立记录测试时间。'),
        '人工核对关键词、数字和单位：',
        '漏字、错字和阅读顺序问题：',
    ]
    destination = Path(destination)
    with tempfile.NamedTemporaryFile(dir=str(destination.parent), suffix='.zip', delete=False) as temporary:
        temporary_path = Path(temporary.name)
    try:
        with zipfile.ZipFile(temporary_path, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            archive.write(original, 'original.png')
            archive.writestr('summary_input.txt', record['text']+'\n')
            archive.writestr('summary.json', json.dumps(summary, ensure_ascii=False, indent=2)+'\n')
            archive.writestr('review.txt', '\n'.join(notes)+'\n')
            for name, path in found.items():
                archive.write(path, name)
            if found:
                archive.writestr('ocr_raw.txt', raw_text+'\n')
        os.replace(temporary_path, destination)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()
    return bool(found)


def export_pages_evidence(store, records, destination):
    destination = Path(destination)
    with tempfile.TemporaryDirectory(dir=str(destination.parent)) as directory:
        staging = Path(directory)
        combined = staging/'all.zip'
        page = staging/'page.zip'
        count = 0
        with zipfile.ZipFile(combined, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            for index, record in enumerate(records, 1):
                count += export_page_evidence(store, record, page)
                prefix = 'P{:02d}-{}'.format(index, record['record_id'])
                with zipfile.ZipFile(page) as sections:
                    for name in sections.namelist():
                        with sections.open(name) as source, archive.open(prefix+'/'+name, 'w') as target:
                            shutil.copyfileobj(source, target)
        os.replace(combined, destination)
    return count
