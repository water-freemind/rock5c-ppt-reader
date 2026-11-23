"""课堂快照/过期检查/分层引用与真实Tk集成，后端替身不冒充NPU验收。"""
import io
from pathlib import Path
import re
import tempfile
import time
import tkinter as tk
from tkinter import ttk
import unittest
from unittest.mock import patch
from PIL import Image
from records import RecordStore
from collections_store import CollectionStore
from collection_summary import CollectionSummarizer, PAGE_TAG, point_references, batches
from test_page_records import summary


class FakeBackend:
    def __init__(self, progress=None, model=None):
        self.calls = []
        self.progress = progress or (lambda event: None)

    def cancel(self):
        pass

    def summarize(self, text, task='page'):
        self.calls.append(text)
        assert task == 'collection'
        refs = sorted(set(re.findall(r'P\d+', ','.join(re.findall(PAGE_TAG, text)))))
        cited = ','.join(refs)
        return dict(title='课堂综合摘要', points=['[{}] 要点{}'.format(cited, i) for i in range(3)],
                    backend='test-double', model='fake', source_text=text, elapsed_seconds=.01,
                    first_token_seconds=.001, tokens_per_second=5, generated_tokens=15,
                    quality_warnings=['测试复核提示'])


class CollectionTests(unittest.TestCase):
    def setUp(self):
        parent = Path(__file__).resolve().parent/'work/storage-tests'
        parent.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=str(parent))
        self.root = Path(self.temp.name)
        self.records = RecordStore(self.root/'data')
        self.pages = []
        for color in ('white', 'gray'):
            image = io.BytesIO()
            Image.new('RGB', (640, 480), color).save(image, format='PNG')
            self.pages.append(self.records.save(image.getvalue(), '原文16GB', summary()))
        self.store = CollectionStore(self.root/'data/collections', self.records)

    def tearDown(self):
        self.temp.cleanup()

    def selections(self):
        return [dict(record_id=r['record_id'], summary_key=r['active_summary_key']) for r in self.pages]

    def test_album_summary_uses_raw_text_and_only_real_page_references(self):
        backend = FakeBackend()
        raw = [dict(page='P1', text='P0 用作地址总线，正文含 [P8]。'),
               dict(page='P2', text='P2 支持外部地址高位。')]
        result = CollectionSummarizer(backend).summarize(raw, raw_ocr=True)
        self.assertIn('P0 用作地址总线', backend.calls[0])
        self.assertIn('［P8］', backend.calls[0])
        self.assertEqual(result['input_kind'], 'album-ocr')
        self.assertEqual(result['point_sources'], [['P1', 'P2']]*3)

    def test_album_ocr_can_be_saved_and_merged_into_classroom_once(self):
        selections = []
        for index, page in enumerate(self.pages):
            text = '相册原文 '+str(index)
            source = self.records.ocr_source(text)
            source['capture_id'] = str(index)
            saved = self.records.save(self.records.image_path(page).read_bytes(), text, source, activate=False)
            self.assertEqual(saved['record_id'], page['record_id'])
            self.assertEqual(saved['active_summary_key'], page['active_summary_key'])
            selections.append(dict(record_id=saved['record_id'], summary_key=self.records.summary_key(source)))

        album_sources = self.store.snapshots({'pages': selections})
        result = CollectionSummarizer(FakeBackend()).summarize(album_sources, raw_ocr=True)
        group, added = self.store.merge_pages('相册课堂', selections, album_results=[result])
        self.assertEqual(added, 2)
        self.assertFalse(self.store.is_stale(group, next(iter(group['results'].values()))))
        repeated, added = self.store.merge_pages(group['name'], selections, group['id'], [result])
        self.assertEqual(added, 0)
        self.assertEqual(len(repeated['results']), 1)

        mixed = self.store.save('已有课堂', self.selections()[:1])
        mixed, added = self.store.merge_pages(mixed['name'], selections, mixed['id'], [result])
        self.assertEqual(added, 1)
        self.assertEqual(mixed['pages'], [self.selections()[0], selections[1]])
        imported = next(iter(mixed['results'].values()))
        self.assertTrue(imported['album_subset'])
        self.assertEqual(imported['sources'][0]['text'], '相册原文 0')
        new_result = CollectionSummarizer(FakeBackend()).summarize(self.store.snapshots(mixed))
        full = self.store.save_result(mixed['id'], self.store.snapshots(mixed), new_result)
        self.assertEqual(len(full['results']), 2)
        self.assertEqual(new_result['input_kind'], 'classroom-pages')

    def test_restart_export_stale_and_preserved_snapshot(self):
        group = self.store.save('Linux课堂', self.selections())
        sources = self.store.snapshots(group)
        result = CollectionSummarizer(FakeBackend()).summarize(sources)
        saved = self.store.save_result(group['id'], sources, result)
        item = next(iter(saved['results'].values()))
        self.assertFalse(self.store.is_stale(saved, item))
        self.assertEqual(CollectionStore(self.store.folder, self.records).load(group['id']), saved)
        self.records.revise(self.pages[0]['record_id'], '原文16GB', '新标题', ['新甲', '新乙', '新丙'])
        self.assertTrue(self.store.is_stale(saved, item))
        with self.assertRaises(ValueError):
            self.store.save_result(group['id'], sources, result)
        self.assertEqual(item['sources'][0]['summary']['title'], '标题')
        destination = self.root/'class.txt'
        self.store.export(saved, destination)
        content = destination.read_text(encoding='utf-8')
        self.assertIn(self.pages[0]['record_id'], content)
        self.assertIn('P1', content)
        self.assertIn('原文16GB', content)
        self.assertIn('测试复核提示', content)

    def test_different_source_edit_does_not_stale_and_reorder_does(self):
        group = self.store.save('课堂', self.selections())
        sources = self.store.snapshots(group)
        saved = self.store.save_result(group['id'], sources, CollectionSummarizer(FakeBackend()).summarize(sources))
        item = next(iter(saved['results'].values()))
        first = self.pages[0]
        self.records.save(self.records.image_path(first).read_bytes(), '原文16GB', summary('deepseek-api', 'deepseek-flash'))
        self.assertFalse(self.store.is_stale(saved, item))
        reordered = self.store.save('课堂', list(reversed(self.selections())), group['id'])
        self.assertTrue(self.store.is_stale(reordered, item))
        with self.assertRaises(ValueError):
            self.store.save('重复页', [self.selections()[0]]*2)

    def test_citations_and_bounded_hierarchical_input(self):
        with self.assertRaises(ValueError):
            point_references(['无引用'], {'P1'})
        with self.assertRaises(ValueError):
            point_references(['[P9] 不存在的页'], {'P1'})
        self.assertEqual(point_references(['事实内容。[P1]'], {'P1'}), [['P1']])
        with self.assertRaises(ValueError):
            batches(['字'*901])
        sources = [dict(page='P'+str(i), summary=dict(title='标题', points=['字'*160]*3)) for i in range(1, 5)]
        backend = FakeBackend()
        result = CollectionSummarizer(backend).summarize(sources)
        self.assertGreater(len(backend.calls), 1)
        self.assertTrue(all(len(text) <= 900 for text in backend.calls))
        self.assertEqual(len(result['point_sources']), 3)
        self.assertEqual(result['input_kind'], 'saved-page-summaries')
        class MissingPage(FakeBackend):
            def summarize(self, text, task='page'):
                value = super().summarize(text, task)
                value['points'] = ['[P1] 只概括第一页']*3
                return value
        with self.assertRaisesRegex(ValueError, '遗漏'):
            short = [dict(page='P'+str(i), summary=dict(title='短页', points=['甲', '乙', '丙'])) for i in (1, 2)]
            CollectionSummarizer(MissingPage()).summarize(short)

    def test_real_tk_save_generate_and_readonly_source_link(self):
        import app as application
        import collections_view as view
        original_root, original_backend = application.ROOT, view.NpuSummarizer
        application.ROOT = self.root
        view.NpuSummarizer = FakeBackend
        root = tk.Tk()
        root.withdraw()
        app = application.App(root)
        try:
            window = app.collections()
            fields = window.collection_fields
            fields['pages'].selection_set(0, 1)
            fields['name'].set('真实Tk测试课堂')
            group = fields['save']()
            self.assertEqual(len(group['pages']), 2)
            fields['generate']()
            self.assertTrue(app.busy)
            # 页面改变不应丢弃独立课堂任务的完成回调。
            app.version += 1
            end = time.monotonic()+5
            while app.busy and time.monotonic() < end:
                root.update()
                time.sleep(.01)
            root.update()
            self.assertFalse(app.busy)
            self.assertIn('已保存综合摘要', fields['status'].get())
            self.assertIn('测试复核提示', fields['output'].get('1.0', 'end'))
            saved = fields['state']['saved']
            self.assertIsNotNone(saved)
            viewer = fields['open_snapshot'](saved, 'P1')
            root.update()
            self.assertTrue(viewer.winfo_exists())
            self.assertIsNone(app.record, '只读来源窗口不能替换主工作台内容')
            viewer.destroy()
            window.destroy()
        finally:
            app.close()
            application.ROOT = original_root
            view.NpuSummarizer = original_backend

    def test_real_tk_deletes_only_the_selected_classroom(self):
        import app as application
        first = self.store.save('同名课堂', self.selections())
        snapshots = self.store.snapshots(first)
        first = self.store.save_result(first['id'], snapshots, CollectionSummarizer(FakeBackend()).summarize(snapshots))
        second = self.store.save('同名课堂', self.selections())
        original_root = application.ROOT
        application.ROOT = self.root
        root = tk.Tk()
        app = application.App(root)
        try:
            window = app.collections()
            root.update()
            fields = window.collection_fields
            body, = window.winfo_children()
            chooser, = [w for w in body.grid_slaves(row=1, column=0) if isinstance(w, ttk.Combobox)]
            toolbar, = body.grid_slaves(row=1, column=1)
            delete, = [w for w in toolbar.winfo_children() if w.text == '删除课堂']
            index = next(i for i, group in enumerate(fields['state']['collections']) if group['id'] == first['id'])
            chooser.current(index)
            chooser.event_generate('<<ComboboxSelected>>')
            self.assertEqual(fields['state']['current']['id'], first['id'])
            self.assertEqual(fields['save']()['pages'], first['pages'])
            fields['order'].selection_set(0)
            fields['move'](1)
            self.assertEqual(fields['save']()['pages'], list(reversed(first['pages'])))
            fields['move'](-1)
            first = fields['save']()
            self.assertIn('课堂综合摘要', fields['output'].get('1.0', 'end'))
            with patch('collections_view.messagebox.askyesno', return_value=False) as confirm:
                delete.invoke()
                self.assertEqual(confirm.call_args.kwargs['default'], 'no')
            self.assertEqual(self.store.load(first['id']), first)
            with patch('collections_view.messagebox.askyesno', return_value=True):
                delete.invoke()
            self.assertIsNone(fields['state']['current'])
            self.assertIsNone(fields['state']['saved'])
            self.assertEqual(fields['pages'].curselection(), ())
            self.assertEqual(chooser.get(), '新课堂')
            self.assertIn('已删除课堂', fields['status'].get())
            reopened = CollectionStore(self.store.folder, self.records)
            self.assertEqual(reopened.list(), [second])
            self.assertEqual(reopened.snapshots(second), snapshots)
            self.assertEqual(len(self.records.list_records()), 2)
            self.assertTrue(all(self.records.image_path(record).is_file() for record in self.pages))
        finally:
            app.close()
            application.ROOT = original_root


if __name__ == '__main__':
    unittest.main()
