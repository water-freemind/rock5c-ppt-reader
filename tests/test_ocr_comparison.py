"""用固定 OCR 结果测试对照保存、字符错误率和窗口回调。"""
import json
from pathlib import Path
import tempfile
import time
import tkinter as tk
import unittest
import numpy as np
from board_app.ocr.ocr_comparison import compare, find_comparison, save_reference, export_comparison, character_error_rate
from shared.paths import PROJECT_ROOT


class FakeOCR:
    def recognize(self, image):
        text = 'ABC1O0' if image.shape[1] == 640 else 'ABC100'
        return dict(text=text, lines=[dict(text=text)], elapsed_seconds=.01, status='ok'), image.copy()


class ComparisonTests(unittest.TestCase):
    def setUp(self):
        parent = PROJECT_ROOT/'work/storage-tests'
        parent.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=str(parent))
        self.root = Path(self.temp.name)
        self.folder = self.root/'data/comparisons'
        self.original = np.full((480, 640, 3), 220, dtype=np.uint8)
        self.corrected = self.original[:400, :500].copy()

    def tearDown(self):
        self.temp.cleanup()

    def test_comparison_reference_export_and_reopen(self):
        result = compare(FakeOCR(), self.original, self.corrected, self.folder, [dict(kind='test-crop')])
        self.assertEqual(find_comparison(self.folder, self.original, self.corrected), result)
        self.assertEqual(find_comparison(self.folder, self.original), result)
        self.assertEqual(result['operations'][0]['kind'], 'test-crop')
        metrics = save_reference(self.folder, result, 'ABC100')
        self.assertAlmostEqual(metrics['original_cer'], 1/6)
        self.assertEqual(metrics['corrected_cer'], 0)
        self.assertNotIn('accuracy', result)
        exported = self.root/'comparison.txt'
        export_comparison(result, exported, metrics)
        self.assertIn('16.67%', exported.read_text(encoding='utf-8'))
        self.assertIn('ABC100', exported.read_text(encoding='utf-8'))
        with self.assertRaises(ValueError):
            character_error_rate('   ', '字')
        with self.assertRaises(ValueError):
            compare(FakeOCR(), self.original, self.original, self.folder)

    def test_real_tk_reopen_saved_comparison_and_calculate(self):
        import board_app.app as application
        original_root = application.ROOT
        application.ROOT = self.root
        root = tk.Tk()
        root.withdraw()
        app = application.App(root)
        try:
            app.set_image(self.original)
            app.engine = FakeOCR()
            app.image = self.corrected
            app.compare_ocr()
            end = time.monotonic()+5
            while app.busy and time.monotonic() < end:
                root.update()
                time.sleep(.01)
            root.update()
            self.assertFalse(app.busy)
            window = next(w for w in root.winfo_children() if hasattr(w, 'comparison_fields'))
            fields = window.comparison_fields
            fields['reference'].insert('1.0', 'ABC100')
            fields['calculate']()
            while app.busy and time.monotonic() < end:
                root.update()
                time.sleep(.01)
            root.update()
            self.assertIn('16.67%', fields['notice'].get())
            window.destroy()
            app.reset_image()
            # 重开原图不重新调用OCR；从已落盘对照直接复查。
            app.engine = None
            app.compare_ocr()
            root.update()
            self.assertFalse(app.busy)
            self.assertIsNone(app.engine)
            reopened = next(w for w in root.winfo_children() if hasattr(w, 'comparison_fields'))
            self.assertEqual(reopened.comparison_fields['reference'].get('1.0', 'end-1c'), 'ABC100')
            reopened.destroy()
        finally:
            if isinstance(app.engine, FakeOCR):
                app.engine = None
            app.close()
            application.ROOT = original_root


if __name__ == '__main__':
    unittest.main()
