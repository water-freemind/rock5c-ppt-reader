"""课堂/报告多页分组与不可变来源快照，编辑原页后明确标记摘要过期。"""
import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import uuid
from shared.records import atomic_json, store_lock, RecordStore


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode('utf-8')).hexdigest()


class CollectionStore:
    def __init__(self, folder, records):
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        self.records = records

    def path(self, identifier):
        if not re.fullmatch('[a-f0-9]{32}', identifier):
            raise ValueError('课堂编号无效')
        return self.folder/(identifier+'.json')

    def load(self, identifier):
        return json.loads(self.path(identifier).read_text(encoding='utf-8'))

    def list(self):
        with store_lock(self.folder):
            collections = [json.loads(p.read_text(encoding='utf-8')) for p in self.folder.glob('*.json')]
        return sorted(collections, key=lambda c: c['updated_at'], reverse=True)

    def delete(self, identifier):
        path = self.path(identifier)
        with store_lock(self.folder):
            path.unlink(missing_ok=True)

    def save(self, name, selections, identifier=None):
        if not name.strip() or not selections:
            raise ValueError('请填写课堂名称并选择至少一页')
        if len({r['record_id'] for r in selections}) != len(selections):
            raise ValueError('同一页在课堂内只能出现一次')
        with store_lock(self.folder):
            collection = self.load(identifier) if identifier else dict(id=uuid.uuid4().hex, results={})
            collection.update(name=name.strip(), pages=copy.deepcopy(selections),
                              updated_at=datetime.now(timezone.utc).isoformat())
            # 校验来源存在，不静默切换另一来源。
            self.snapshots(collection)
            atomic_json(self.path(collection['id']), collection)
        return collection

    def snapshots(self, collection):
        result = []
        for index, reference in enumerate(collection['pages'], 1):
            record = self.records.load(reference['record_id'])
            if reference['summary_key'] not in record['summaries']:
                record = next(r for r in self.records.list_records() if r['record_id'] == reference['record_id'])
            summary = record['summaries'][reference['summary_key']]
            result.append(dict(page='P'+str(index), record_id=record['record_id'],
                               summary_key=reference['summary_key'], image=record['image'],
                               image_sha256=record['image_sha256'], captured_at=record['captured_at'],
                               text=summary.get('source_text', record['text']), summary=copy.deepcopy(summary)))
        return result

    def merge_pages(self, name, selections, identifier=None, album_results=()):
        """Append unique records and keep album summaries linked to their original page order."""
        if not selections or not name.strip():
            raise ValueError('请填写课堂名称并选择至少一页')
        with store_lock(self.folder):
            collection = self.load(identifier) if identifier else dict(id=uuid.uuid4().hex, name=name.strip(),
                                                                       pages=[], results={})
            known = {page['record_id'] for page in collection['pages']}
            added = 0
            for selection in selections:
                if selection['record_id'] not in known:
                    collection['pages'].append(copy.deepcopy(selection))
                    known.add(selection['record_id'])
                    added += 1
            classroom = self.snapshots(collection)
            if album_results:
                sources = self.snapshots(dict(pages=selections))
                labels = [source['page'] for source in sources]
                complete = digest(classroom) == digest(sources)
                for result in album_results:
                    if result.get('source_pages') != labels:
                        raise ValueError('相册摘要与选中的页面不一致')
                    key = RecordStore.summary_key(result)
                    if not complete:
                        key = 'album:'+digest(sources)+':'+key
                    item = self.result_entry(sources, result)
                    if not complete:
                        item['album_subset'] = True
                    collection['results'][key] = item
            collection['updated_at'] = datetime.now(timezone.utc).isoformat()
            atomic_json(self.path(collection['id']), collection)
        return collection, added

    @staticmethod
    def result_entry(snapshots, result):
        return dict(summary=copy.deepcopy(result), sources=copy.deepcopy(snapshots),
                    input_digest=digest(snapshots), generated_at=datetime.now(timezone.utc).isoformat())

    def save_result(self, identifier, snapshots, result):
        with store_lock(self.folder):
            collection = self.load(identifier)
            if digest(snapshots) != digest(self.snapshots(collection)):
                raise ValueError('课堂页序或来源内容已改变，请重新生成综合摘要')
            key = RecordStore.summary_key(result)
            collection['results'][key] = self.result_entry(snapshots, result)
            collection['updated_at'] = datetime.now(timezone.utc).isoformat()
            atomic_json(self.path(identifier), collection)
        return collection

    def is_stale(self, collection, result):
        try:
            return result['input_digest'] != digest(self.snapshots(collection))
        except (OSError, ValueError, KeyError):
            return True

    @staticmethod
    def export(collection, destination):
        lines = [collection['name'], '课堂编号：'+collection['id']]
        for saved in collection['results'].values():
            result = saved['summary']
            scope = '相册选页摘要' if saved.get('album_subset') else '综合摘要'
            lines += ['', scope+'来源：'+RecordStore.source_label(result), result['title']]
            lines += ['- '+p for p in result['points']]
            if result.get('quality_warnings'):
                lines += ['需人工复核：']+result['quality_warnings']
            lines += ['', '来源快照（生成时内容，后续编辑不会改写此快照）：']
            for source in saved['sources']:
                lines += [source['page']+' · '+source['summary']['title'],
                          '页面编号：'+source['record_id'], '原图：'+source['record_id']+'/'+source['image'],
                          '来源：'+RecordStore.source_label(source['summary']), source['text']]
        Path(destination).write_text('\n'.join(lines)+'\n', encoding='utf-8')
