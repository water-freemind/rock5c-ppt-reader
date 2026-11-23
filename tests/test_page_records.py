"""记录与接收端修订逻辑验证，完全不运行模型、不调用API。"""
import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from shared.records import RecordStore
from windows_receiver.receiver import persist_record
from board_app.summary.credentials import CredentialStore
from shared.paths import PROJECT_ROOT


def summary(backend='local-rkllm-npu', model='Qwen3-4B', text='原文16GB'):
    return dict(title='标题', points=['第一条', '第二条', '第三条'], source_text=text,
                backend=backend, model=model)


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.test_parent = PROJECT_ROOT/'work/storage-tests'
        self.test_parent.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=str(self.test_parent))
        self.root = Path(self.temp.name)
        self.store = RecordStore(self.root/'data')

    def tearDown(self):
        assert self.root.resolve().parent == self.test_parent.resolve()
        self.temp.cleanup()

    def test_same_page_different_sources_and_restart(self):
        first = self.store.save(b'image', '原文16GB', summary())
        second = self.store.save(b'image', '原文16GB', summary('deepseek-api', 'deepseek-flash'))
        self.assertEqual(first['record_id'], second['record_id'])
        self.assertEqual(len(second['summaries']), 2)
        self.assertEqual(second['revision'], 2)
        self.assertEqual(len(self.store.list_records()), 1)
        self.assertEqual(len(list((self.root/'data').glob('*/record.json'))), 1)
        self.assertEqual(RecordStore(self.root/'data').load(first['record_id']), second)
        same = self.store.save(b'image', '原文16GB', summary('deepseek-api', 'deepseek-flash'))
        self.assertEqual(same, second)
        self.store.export(second, self.root/'export.txt')
        exported = (self.root/'export.txt').read_text(encoding='utf-8')
        self.assertIn('Qwen3-4B', exported)
        self.assertIn('deepseek-flash', exported)

    def test_edit_selected_source_keeps_other_source(self):
        one = self.store.save(b'image', '原文16GB', summary())
        two = self.store.save(b'image', '原文16GB', summary('deepseek-api', 'deepseek-flash'))
        key = self.store.summary_key(one['summary'])
        changed = self.store.revise(two['record_id'], '原文16GB', '校订标题', ['甲', '乙', '丙'], summary_key=key)
        self.assertEqual(changed['record_id'], one['record_id'])
        self.assertEqual(changed['summaries'][key]['title'], '校订标题')
        self.assertEqual(changed['summaries'][self.store.summary_key(two['summary'])], two['summary'])
        with self.assertRaises(ValueError):
            self.store.revise(changed['record_id'], '改动后的原文', '标题', ['甲', '乙', '丙'])
        selected = self.store.select_summary(changed, self.store.summary_key(two['summary']))
        self.assertEqual(self.store.transport_record(selected), self.store.load(changed['record_id']))

    def test_old_duplicate_pages_visible_once_without_deletion(self):
        for index in (1, 2):
            identifier = str(index)*32
            folder = self.root/'data'/identifier
            folder.mkdir()
            (folder/'original.png').write_bytes(b'legacy-image')
            value = dict(record_id=identifier, captured_at='2026-01-0{}T00:00:00Z'.format(index),
                         text='原文16GB', image='original.png', metadata={},
                         summary=summary('old-'+str(index), 'model'))
            (folder/'record.json').write_text(json.dumps(value), encoding='utf-8')
        pages = self.store.list_records()
        self.assertEqual(len(pages), 1)
        self.assertEqual(len(pages[0]['summaries']), 2)
        self.assertEqual(len(list((self.root/'data').glob('*/record.json'))), 2)
        saved = self.store.save(b'legacy-image', '原文16GB', summary())
        self.assertEqual(saved['record_id'], '1'*32)
        self.assertEqual(len(saved['summaries']), 3)

    def test_receiver_accepts_newer_revision_and_rejects_conflicts(self):
        received = self.root/'received'
        received.mkdir()
        first = self.store.save(b'image', '原文16GB', summary())
        second = self.store.save(b'image', '原文16GB', summary('deepseek-api', 'deepseek-flash'))
        persist_record(received, first)
        persist_record(received, first)
        persist_record(received, second)
        self.assertEqual(len(list(received.glob('*.json'))), 1)
        self.assertEqual(json.loads((received/(first['record_id']+'.json')).read_text(encoding='utf-8')), second)
        with self.assertRaises(ValueError):
            persist_record(received, first)
        conflict = copy.deepcopy(second)
        conflict['text'] = '不同内容但相同修订'
        with self.assertRaises(ValueError):
            persist_record(received, conflict)

    def test_credentials_private_restart_and_validation(self):
        store = CredentialStore(self.root/'private')
        self.assertEqual(store.load(), '')
        store.save('test-value-not-a-real-key')
        self.assertEqual(CredentialStore(self.root/'private').load(), 'test-value-not-a-real-key')
        if os.name == 'posix':
            self.assertEqual(store.path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(store.folder.stat().st_mode & 0o777, 0o700)
        with self.assertRaises(ValueError):
            store.save('invalid\nvalue')
        store.save('')
        self.assertEqual(store.load(), '')


if __name__ == '__main__':
    unittest.main()
