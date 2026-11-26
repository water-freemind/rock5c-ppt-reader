"""同页图片一条记录，摘要按来源归并；修订编号用于TCP幂等更新。"""
from pathlib import Path
from datetime import datetime, timezone
import json
import uuid
import os
import re
import copy
import hashlib
import tempfile
from contextlib import contextmanager


@contextmanager
def store_lock(root):
    with (root/'.records.lock').open('a+b') as stream:
        if os.name == 'nt':
            import msvcrt
            if stream.tell() == 0:
                stream.write(b'0')
                stream.flush()
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(stream, fcntl.LOCK_EX)
        try:
            yield
        finally:
            if os.name == 'nt':
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)


def atomic_json(path, value):
    fd, temporary = tempfile.mkstemp(prefix='.'+path.name+'-', dir=str(path.parent))
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


OCR_BACKEND = 'rknn-ocr'


class RecordStore:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def ocr_source(text, title=None):
        return dict(backend=OCR_BACKEND, model='PP-OCRv4', source_text=text.strip(),
                    title=title or text.strip().splitlines()[0][:48], points=[])

    def save(self, image_bytes, text, summary, metadata=None, *, activate=True):
        if not text.strip():
            raise ValueError('原文为空，不能保存')
        if summary.get('source_text') != text.strip():
            raise ValueError('原文已修改，请重新生成摘要后保存')
        # 用图片指纹找出已有页面，用摘要后端和模型名标记该页的不同摘要。
        key = self.summary_key(summary)
        fingerprint = hashlib.sha256(image_bytes).hexdigest()
        with store_lock(self.root):
            records = self.list_records()
            existing = next((r for r in records if r.get('image_sha256') == fingerprint), None)
            capture_id = summary.get('capture_id')
            if existing is None and capture_id:
                existing = next((r for r in records if any(s.get('capture_id') == capture_id
                                for s in r['summaries'].values())), None)
                if existing:
                    fingerprint = existing['image_sha256']
            now = datetime.now(timezone.utc).isoformat()
            if existing:
                record = copy.deepcopy(existing)
                folder = self.root/record['record_id']
                disk = json.loads((folder/'record.json').read_text(encoding='utf-8'))
                if (disk.get('schema_version') == 2 and disk.get('summaries') == record['summaries']
                        and record['summaries'].get(key) == summary):
                    self.export(record, folder/'result.txt')
                    return record
                # 写入新修订前，先归档上一份记录，方便追溯编辑历史。
                archive = folder/'revisions'
                archive.mkdir(exist_ok=True)
                archive_key = hashlib.sha256(json.dumps(disk, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
                if not (archive/(archive_key+'.json')).exists():
                    atomic_json(archive/(archive_key+'.json'), disk)
                record['revision'] = int(disk.get('revision', 0))+1
            else:
                record_id = uuid.uuid4().hex
                folder = self.root/record_id
                folder.mkdir()
                (folder/'original.png').write_bytes(image_bytes)
                record = dict(record_id=record_id, image='original.png', revision=1, summaries={},
                              captured_at=(metadata or {}).get('captured_at') or now, metadata={}, text=text.strip())
            active = record['active_summary_key'] if existing and not activate else key
            record.update(schema_version=2, image_sha256=fingerprint, revision_id=uuid.uuid4().hex,
                          updated_at=now)
            record['summaries'][key] = copy.deepcopy(summary)
            record = self.select_summary(record, active)
            record['metadata'].update(metadata or {})
            record['metadata']['captured_at'] = record['captured_at']
            atomic_json(folder/'record.json', record)
            self.export(record, folder/'result.txt')
            return record

    @staticmethod
    def summary_key(summary):
        return json.dumps([summary.get('backend', 'unknown'), summary.get('model', 'legacy')], ensure_ascii=False)

    @staticmethod
    def source_label(summary):
        backend = summary.get('backend', 'unknown')
        provider = {'local-rkllm-npu': '本地NPU', 'deepseek-api': 'DeepSeek',
                    'local-llama.cpp': '历史CPU摘要', 'manual-review': '人工校订',
                    OCR_BACKEND: 'OCR 原文'}.get(backend, backend)
        return provider+' / '+summary.get('model', '旧版本')+('（已校订）' if summary.get('manually_edited') else '')

    def image_path(self, record):
        folder = self.root/record['record_id']
        image = folder/record['image']
        if image.resolve().parent != folder.resolve():
            raise ValueError('历史图片路径不合法')
        return image

    def normalize(self, record):
        record = copy.deepcopy(record)
        key = self.summary_key(record['summary'])
        record.setdefault('summaries', {key: record['summary']})
        record.setdefault('active_summary_key', key)
        if 'image_sha256' not in record:
            try:
                record['image_sha256'] = hashlib.sha256(self.image_path(record).read_bytes()).hexdigest()
            except OSError:
                record['image_sha256'] = None
        return record

    @staticmethod
    def select_summary(record, key):
        selected = copy.deepcopy(record)
        selected['summary'] = copy.deepcopy(record['summaries'][key])
        selected['text'] = selected['summary'].get('source_text', record['text'])
        selected['active_summary_key'] = key
        return selected

    def load(self, record_id):
        if not re.fullmatch('[a-f0-9]{32}', record_id):
            raise ValueError('记录编号不合法')
        record = json.loads((self.root/record_id/'record.json').read_text(encoding='utf-8'))
        if record['record_id'] != record_id:
            raise ValueError('记录编号与目录不一致')
        return self.normalize(record)

    def revise(self, record_id, text, title, points, source_reviewed=False, summary_key=None):
        """只更新该来源摘要，页记录编号保持不变，旧快照内部归档。"""
        original = self.load(record_id)
        original = next((r for r in self.list_records() if r['record_id'] == record_id), original)
        if summary_key:
            original = self.select_summary(original, summary_key)
        ocr_only = original['summary'].get('backend') == OCR_BACKEND
        if not ocr_only and text.strip() != original['text'] and not source_reviewed:
            raise ValueError('原文已修改，请核对摘要并勾选确认，或返回工作台重新生成')
        if not title.strip():
            raise ValueError('请输入标题')
        if not ocr_only and (not 3 <= len(points) <= 5 or not all(p.strip() for p in points)):
            raise ValueError('请输入标题和3～5条非空摘要要点，每行一条')
        summary = copy.deepcopy(original['summary'])
        if text.strip() != original['text']:
            summary.pop('knowledge', None)
        summary.update(title=title.strip(), points=[] if ocr_only else [p.strip() for p in points],
                       source_text=text.strip(), manually_edited=True)
        metadata = dict(original.get('metadata', {}))
        metadata['edited_at'] = datetime.now(timezone.utc).isoformat()
        metadata['ocr_manually_reviewed' if ocr_only else 'summary_manually_reviewed'] = True
        saved = self.save(self.image_path(original).read_bytes(), text, summary, metadata, activate=not ocr_only)
        return self.select_summary(saved, self.summary_key(summary))

    def list_records(self, include_versions=False):
        result = []
        for path in self.root.glob('*/record.json'):
            try:
                value = json.loads(path.read_text(encoding='utf-8'))
                if value['record_id'] == path.parent.name and re.fullmatch('[a-f0-9]{32}', value['record_id']):
                    result.append(self.normalize(value))
            except (OSError, ValueError, KeyError, TypeError):
                continue
        if not include_versions:
            groups = {}
            for record in result:
                groups.setdefault(record.get('image_sha256') or record['record_id'], []).append(record)
            result = []
            for records in groups.values():
                merged = copy.deepcopy(min(records, key=lambda r: (r['captured_at'], r['record_id'])))
                variants = {}
                ordered = sorted(records, key=lambda r: r.get('updated_at', r['captured_at']))
                for record in ordered:
                    variants.update(record['summaries'])
                merged['summaries'] = variants
                latest_key = ordered[-1]['active_summary_key']
                merged = self.select_summary(merged, latest_key)
                result.append(merged)
        return sorted(result, key=lambda r: r.get('updated_at', r['captured_at']), reverse=True)

    def transport_record(self, record):
        """发送磁盘上的完整修订，避免历史界面的临时来源选择改变消息内容。"""
        current = self.load(record['record_id'])
        if current.get('schema_version') != 2:
            current = self.save(self.image_path(current).read_bytes(), current['text'], current['summary'], current.get('metadata'))
        return self.load(current['record_id'])

    @staticmethod
    def export(record, destination):
        raw = record['summary'].get('ocr_raw_text')
        heading = '机器 OCR 原文' if raw is not None else '当前摘要输入'
        content = '{}\n{}\n\n{}\n{}\n'.format(record['record_id'], record['captured_at'],
                                                  heading, raw if raw is not None else record['text'])
        for summary in record.get('summaries', {'legacy': record['summary']}).values():
            label = '记录来源' if summary.get('backend') == OCR_BACKEND else '摘要来源'
            content += '\n'+label+'：'+RecordStore.source_label(summary)+'\n'
            source = summary.get('source_text', record['text'])
            if source != (raw if raw is not None else record['text']):
                content += '对应摘要输入\n'+source+'\n'
            content += summary['title']+'\n'+'\n'.join('- '+p for p in summary['points'])+'\n'
            if summary.get('quality_warnings'):
                content += '\n需人工复核\n'+'\n'.join(summary['quality_warnings'])+'\n'
            if summary.get('knowledge'):
                knowledge = summary['knowledge']
                content += '\n知识补充（AI 生成） · '+knowledge['model']+'\n'
                content += '\n'.join('- '+note for note in knowledge['notes'])+'\n'
        Path(destination).write_text(content, encoding='utf-8')

    @staticmethod
    def export_markdown(record, destination):
        def inline(text):
            # OCR 和摘要可能含 Markdown 符号；保留字面内容，不生成意外的链接或标签。
            return re.sub(r'([\\`*_\[\]<>])', r'\\\1', ' '.join(text.strip().splitlines()))

        def source_block(text):
            longest = max((len(run) for run in re.findall(r'`+', text)), default=0)
            fence = '`' * max(3, longest+1)
            return [fence+'text', text, fence]

        variants = record.get('summaries', {'legacy': record['summary']})
        raw = record['summary'].get('ocr_raw_text')
        heading = '## 机器 OCR 原文' if raw is not None else '## OCR 原文 / 已保存的摘要输入'
        displayed_source = raw if raw is not None else record['text']
        content_heading = '## 已保存内容' if any(s.get('backend') == OCR_BACKEND for s in variants.values()) else '## 摘要'
        lines = ['# '+inline(record['summary']['title']), '',
                 '- 记录编号：`'+record['record_id']+'`',
                 '- 采集时间：'+inline(record['captured_at']), '',
                 heading, '', *source_block(displayed_source), '',
                 content_heading, '']
        for summary in variants.values():
            lines.extend(['### '+inline(RecordStore.source_label(summary)), '',
                          '**'+inline(summary['title'])+'**', ''])
            lines.extend('- '+inline(point) for point in summary['points'])
            lines.append('')
            source = summary.get('source_text', record['text'])
            if summary.get('ocr_raw_text') is not None and summary['ocr_raw_text'] != raw:
                lines.extend(['#### 对应机器 OCR 原文', '', *source_block(summary['ocr_raw_text']), ''])
            if source != displayed_source:
                lines.extend(['#### 对应校订原文', '', *source_block(source), ''])
            if summary.get('review_history'):
                lines.extend(['#### 已确认的校对操作', ''])
                for action in summary['review_history']:
                    if action['kind'] == 'ai-review':
                        lines.extend('- '+inline(action['model']+' 建议：'+item['original']+' → '+item['replacement'])
                                     for item in action['edits'])
                    elif action['kind'] == 'region-ocr':
                        lines.append('- 框选区域 OCR：'+inline(action['text']))
                lines.append('')
            if summary.get('quality_warnings'):
                lines.extend(['#### 需人工复核', ''])
                lines.extend('- '+inline(warning) for warning in summary['quality_warnings'])
                lines.append('')
        for summary in variants.values():
            if summary.get('knowledge'):
                knowledge = summary['knowledge']
                lines.extend(['## 知识补充', '', 'AI 生成 · '+inline(knowledge['model'])+
                              ' · 对应摘要 '+inline(RecordStore.source_label(summary)), ''])
                lines.extend('- '+inline(note) for note in knowledge['notes'])
                lines.append('')
        Path(destination).write_text('\n'.join(lines).rstrip()+'\n', encoding='utf-8')
