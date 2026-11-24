"""板端真实Tk历史编辑/来源切换/队列窗口验证；隔离数据，不进行推理。"""
import io
import json
from pathlib import Path
import tempfile
import time
import tkinter as tk
import unittest
import zipfile
from unittest.mock import patch
from PIL import Image
from board_app.storage.page_evidence import export_pages_evidence
from board_app.ui.ui_layout import ActionButton
import board_app.app as application
from tests.test_page_records import summary
from shared.paths import PROJECT_ROOT


class HistoryGuiTests(unittest.TestCase):
    def test_history_search_reopens_draft_without_evidence_export_buttons(self):
        original_root = application.ROOT
        with tempfile.TemporaryDirectory() as temporary:
            application.ROOT = Path(temporary)
            root = tk.Tk()
            root.withdraw()
            app = application.App(root)
            try:
                for color, text in (('white', '教学串行总线'), ('gray', '寄存器复习')):
                    image = io.BytesIO()
                    Image.new('RGB', (64, 48), color).save(image, format='PNG')
                    app.store.save(image.getvalue(), text, dict(summary(), source_text=text))
                fields = app.history().history_fields
                fields['query'].set('串行总线')
                self.assertEqual(fields['pages'].size(), 1)
                fields['load']()
                first = app.saved_page
                app.text.insert('end', ' 校对稿')
                root.update()
                fields = app.history().history_fields
                fields['query'].set('寄存器')
                fields['load']()
                self.assertTrue(app.draft_path(first).is_file())
                fields = app.history().history_fields
                fields['query'].set('串行总线')
                fields['load']()
                self.assertEqual(app.text.get('1.0', 'end-1c'), '教学串行总线 校对稿')
                self.assertIsNone(app.record)
                self.assertEqual(app.store.load(first['record_id'])['text'], '教学串行总线')
                self.assertTrue(app.text.cget('undo'))
                history = app.history()
                self.assertEqual(history.history_fields['pages'].cget('selectmode'), 'browse')
                widgets = list(root.winfo_children())
                buttons = set()
                while widgets:
                    widget = widgets.pop()
                    if isinstance(widget, ActionButton):
                        buttons.add(widget.text)
                    widgets.extend(widget.winfo_children())
                self.assertNotIn('导出验收材料', buttons)
                self.assertNotIn('批量导出验收材料', buttons)
                self.assertIn('导出笔记', buttons)
                self.assertIn('发送到电脑', buttons)
                path = Path(temporary)/'evidence.zip'
                export_pages_evidence(app.store, app.store.list_records(), path)
                with zipfile.ZipFile(path) as archive:
                    self.assertEqual(len([name for name in archive.namelist() if name.endswith('/original.png')]), 2)
            finally:
                app.close()
                application.ROOT = original_root

    def test_generated_summaries_auto_save_on_history_page(self):
        class Backend:
            def __init__(self, backend, model):
                self.value = summary(backend, model)

            def summarize(self, text):
                return dict(self.value, source_text=text, first_token_seconds=0.1, elapsed_seconds=0.2)

            def cancel(self):
                pass

        original_root = application.ROOT
        with tempfile.TemporaryDirectory() as temporary:
            application.ROOT = Path(temporary)
            root = tk.Tk()
            root.withdraw()
            app = application.App(root)
            try:
                image = io.BytesIO()
                Image.new('RGB', (64, 48), 'white').save(image, format='PNG', compress_level=0)
                original = app.store.save(image.getvalue(), '原文16GB', summary('local-llama.cpp', 'legacy'))
                app.history().history_fields['load']()
                app.text.insert('end', ' · 已校对')
                root.update()
                for provider, model, target, backend in (
                    ('本地 NPU', app.summary_model.get(), 'NpuSummarizer', 'local-rkllm-npu'),
                    ('DeepSeek API', 'deepseek-flash', 'DeepSeekSummarizer', 'deepseek-api'),
                ):
                    app.summary_provider.set(provider)
                    app.summary_model.set(model)
                    generated = Backend(backend, model)
                    with patch('board_app.ui.summary_controls.'+target, return_value=generated):
                        app.summarize()
                        deadline = time.monotonic()+5
                        while app.busy and time.monotonic() < deadline:
                            root.update()
                            time.sleep(.01)
                    self.assertFalse(app.busy)
                    self.assertIn('已自动保存', app.summary_run.get())
                    self.assertEqual(app.record['record_id'], original['record_id'])
                    self.assertEqual(app.record['summary']['source_text'], '原文16GB · 已校对')
                saved = app.store.load(original['record_id'])
                self.assertEqual(len(app.store.list_records(include_versions=True)), 1)
                self.assertEqual(len(saved['summaries']), 3)
                self.assertEqual(len(app.saved_sources['values']), 3)
                self.assertEqual(app.store.image_path(saved).read_bytes(), image.getvalue())
                self.assertEqual(saved['summaries'][original['active_summary_key']], original['summary'])
            finally:
                app.close()
                application.ROOT = original_root

    def test_sources_edit_restart_and_queue_dialog(self):
        base = PROJECT_ROOT
        parent = base/'work/storage-tests'
        parent.mkdir(parents=True, exist_ok=True)
        original_root = application.ROOT
        with tempfile.TemporaryDirectory(dir=str(parent)) as temporary:
            application.ROOT = Path(temporary)
            root = tk.Tk()
            root.withdraw()
            app = application.App(root)
            try:
                image = io.BytesIO()
                Image.new('RGB', (640, 480), 'white').save(image, format='PNG')
                one = app.store.save(image.getvalue(), '原文16GB', summary())
                two = app.store.save(image.getvalue(), '原文16GB', summary('deepseek-api', 'deepseek-flash'))
                history = app.history()
                root.update()
                fields = history.history_fields
                self.assertEqual(fields['pages'].size(), 1)
                self.assertEqual(len(fields['source']['values']), 2)
                fields['source'].current(0)
                fields['edit']()
                root.update()
                editor = next(w for w in history.winfo_children() if hasattr(w, 'editor_fields'))
                edit = editor.editor_fields
                edit['title'].set('历史人工校订')
                edit['source'].delete('1.0', 'end')
                edit['source'].insert('1.0', '校订后的原文16GB')
                edit['commit']()
                self.assertIn('原文已修改', edit['status'].get())
                edit['reviewed'].set(True)
                edit['commit']()  # 回调会销毁父窗口，不能再次销毁导致Tcl错误。
                root.update()
                self.assertFalse(history.winfo_exists())
                self.assertEqual(app.record['record_id'], one['record_id'])
                self.assertEqual(app.summary['title'], '历史人工校订')
                self.assertEqual(len(app.record['summaries']), 2)
                history = app.history()
                fields = history.history_fields
                fields['source'].current(1)
                fields['load']()
                self.assertEqual(app.summary['backend'], 'deepseek-api')
                self.assertEqual(app.text.get('1.0', 'end-1c'), '原文16GB')
                self.assertIn('DeepSeek', app.summary_run.get())
                self.assertEqual(len(app.saved_sources['values']), 2)
                self.assertEqual(app.saved_sources.current(), 1)
                app.saved_sources.current(0)
                app.switch_saved_summary()
                root.update()
                self.assertEqual(app.summary['title'], '历史人工校订')
                self.assertEqual(app.text.get('1.0', 'end-1c'), '校订后的原文16GB')
                self.assertEqual(app.record['record_id'], one['record_id'])
                app.summary_provider.set('DeepSeek API')
                app.summary_combos[0].event_generate('<<ComboboxSelected>>')
                root.update()
                self.assertEqual(app.summary['title'], '历史人工校订')
                app.text.insert('end', '草稿')
                root.update()
                app.saved_sources.current(1)
                with patch('board_app.app.messagebox.askokcancel', return_value=False) as confirm:
                    app.switch_saved_summary()
                    confirm.assert_called_once()
                self.assertTrue(app.text.get('1.0', 'end-1c').endswith('草稿'))
                app.saved_sources.current(1)
                with patch('board_app.app.messagebox.askokcancel', return_value=True):
                    app.switch_saved_summary()
                root.update()
                self.assertEqual(app.summary['backend'], 'deepseek-api')
                self.assertEqual(app.text.get('1.0', 'end-1c'), '原文16GB')
                self.assertEqual(len(app.store.list_records()), 1)
                dialog = app.pending_transmissions()
                root.update()
                self.assertIs(dialog.master, app.page_area)
                self.assertEqual(app.page_title.get(), '发送队列')
                self.assertFalse(app.workbench.winfo_manager())
                for action in (app.capture_album, app.collections, app.history):
                    page = action()
                    self.assertIs(page.master, app.page_area)
                self.assertFalse(any(isinstance(widget, tk.Toplevel) for widget in root.winfo_children()))
                app.show_workspace()
                self.assertTrue(app.workbench.winfo_manager())
                self.assertEqual(app.record['record_id'], one['record_id'])
                result = dict(status='passed', scope='isolated actual Tk; no NPU or API inference',
                              checks=['one page two sources', 'edit review gate', 'parent dialog close',
                                      'workbench source switch', 'generation selection preserves saved summary',
                                      'cancel keeps draft', 'source-specific original text', 'embedded sidebar views'])
                evidence = base/'evidence'
                evidence.mkdir(exist_ok=True)
                (evidence/'history_gui.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
            finally:
                app.close()
                application.ROOT = original_root


if __name__ == '__main__':
    unittest.main()
