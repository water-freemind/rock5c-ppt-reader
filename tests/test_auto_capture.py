"""生成帧验证稳定/变化/质量筛选和落盘恢复，不冒充真实摄像头测试。"""
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import cv2
import numpy as np
from board_app.capture.auto_capture import AutoCollector, StablePageDetector, comparison_thumbnail, signature
from shared.paths import PROJECT_ROOT


def page(seed):
    generator = np.random.RandomState(seed)
    image = np.full((360, 640, 3), 225, dtype=np.uint8)
    for row in range(8):
        cv2.putText(image, 'P{} {} MB'.format(seed, generator.randint(100, 999)),
                    (25, 35+row*40), cv2.FONT_HERSHEY_SIMPLEX, .8, (20, 20, 20), 2)
    return image


class AutoCaptureTests(unittest.TestCase):
    def test_stability_change_and_return_dedup(self):
        first, second = page(1), page(7)
        detector = StablePageDetector()
        self.assertIsNone(detector.observe(first, 0))
        self.assertIsNone(detector.observe(first, 1))
        result = detector.observe(first, 2.1)
        self.assertIsNotNone(result)
        detector.accepted(result['signature'])
        self.assertIsNone(detector.observe(first, 3))
        self.assertIsNone(detector.observe(second, 4))
        result = detector.observe(second, 6.1)
        self.assertIsNotNone(result)
        detector.accepted(result['signature'])
        self.assertIsNone(detector.observe(first, 7))
        self.assertIsNone(detector.observe(first, 9.1))
        self.assertIn('重复', detector.note)

    def test_dark_blank_blur_and_motion_rejected(self):
        detector = StablePageDetector()
        for image in [np.zeros((360, 640, 3), dtype=np.uint8),
                      np.full((360, 640, 3), 200, dtype=np.uint8),
                      cv2.GaussianBlur(page(1), (51, 51), 20)]:
            self.assertIsNone(detector.observe(image, 0))
            self.assertIsNone(detector.observe(image, 10))
        for index in range(10):
            self.assertIsNone(detector.observe(page(index+10), index+20))

    def test_exposure_and_small_flicker_do_not_restart_stability_timer(self):
        original = page(1)
        detector = StablePageDetector()
        self.assertIsNone(detector.observe(original, 0))
        flicker = np.clip(original.astype(np.int16)+10, 0, 255).astype(np.uint8)
        flicker[48:57, :] -= 22
        self.assertIsNone(detector.observe(flicker, 1))
        self.assertIsNotNone(detector.observe(original, 2.1))

    def test_hand_jitter_and_brief_bad_frame_allow_capture_without_duplicates(self):
        original = page(1)
        detector = StablePageDetector()

        def shifted(x, y, angle=0):
            transform = cv2.getRotationMatrix2D((320, 180), angle, 1)
            transform[:, 2] += (x, y)
            return cv2.warpAffine(original, transform, (640, 360), borderMode=cv2.BORDER_REPLICATE)

        self.assertIsNone(detector.observe(original, 0))
        self.assertIsNone(detector.observe(shifted(12, -8, .45), .4))
        self.assertIsNone(detector.observe(shifted(-8, 8, -.45), .8))
        self.assertIsNone(detector.observe(np.zeros_like(original), 1.1))
        self.assertIsNone(detector.observe(shifted(4, 4), 1.2))
        result = detector.observe(shifted(8, -4), 2.2)
        self.assertIsNotNone(result)
        detector.accepted(result['signature'])
        self.assertIsNone(detector.observe(shifted(-8, -4), 2.4))
        self.assertIn('重复', detector.note)

    def test_moving_slide_requires_a_new_stable_period(self):
        original = page(1)
        detector = StablePageDetector()
        self.assertIsNone(detector.observe(original, 0))
        for index in range(1, 10):
            transform = np.float32([[1, 0, 12*index], [0, 1, 0]])
            moving = cv2.warpAffine(original, transform, (640, 360), borderMode=cv2.BORDER_REPLICATE)
            self.assertIsNone(detector.observe(moving, index*.3))
        self.assertIsNone(detector.observe(page(7), 3.0))
        self.assertIsNone(detector.observe(page(7), 4.6))
        self.assertIsNotNone(detector.observe(page(7), 5.1))

    def test_white_background_with_crisp_text_can_be_saved(self):
        bright = page(1)
        bright[bright == 225] = 255
        detector = StablePageDetector()
        self.assertGreater(bright.mean(), 245)
        self.assertIsNone(detector.observe(bright, 0))
        self.assertIsNotNone(detector.observe(bright, 2.1))

    def test_stop_finishes_last_queued_frame(self):
        first_frame = threading.Event()
        release = threading.Event()
        with tempfile.TemporaryDirectory() as temporary:
            collector = AutoCollector(temporary)
            observe = collector.detector.observe
            image = page(1)

            def wait_for_stop(frame, captured):
                if not first_frame.is_set():
                    first_frame.set()
                    release.wait(3)
                return observe(frame, captured)

            collector.detector.observe = wait_for_stop
            try:
                with patch('board_app.capture.auto_capture.time.monotonic', side_effect=[0.0, 2.1]):
                    collector.submit(image)
                    self.assertTrue(first_frame.wait(2))
                    collector.submit(image)
                collector.close()
                release.set()
                collector.thread.join(3)
                self.assertEqual(collector.saved_count, 1)
                self.assertEqual(len(collector.list_pages()), 1)
            finally:
                collector.close()
                release.set()
                collector.thread.join(3)

    def test_stable_scene_does_not_resave_when_detail_fingerprint_changes(self):
        original = page(1)
        detector = StablePageDetector()
        self.assertIsNone(detector.observe(original, 0))
        result = detector.observe(original, 2.1)
        detector.accepted(result['signature'], comparison_thumbnail(original))
        noise = np.random.RandomState(12).randint(-8, 9, original.shape[:2])
        noisy = (original.astype(np.int16)+noise[:, :, None]).astype(np.uint8)
        self.assertGreater(np.abs(comparison_thumbnail(original)-comparison_thumbnail(noisy)).mean(), 1.5)
        self.assertIsNone(detector.observe(noisy, 3.0))
        self.assertIn('重复', detector.note)
        self.assertIsNone(detector.observe(page(7), 4.0))
        self.assertIsNotNone(detector.observe(page(7), 6.1))

    def test_recompression_dedup_keeps_page_with_changed_line(self):
        original = page(1)
        detector = StablePageDetector()
        detector.accepted(signature(original), comparison_thumbnail(original))
        encoded = cv2.imencode('.jpg', original, [cv2.IMWRITE_JPEG_QUALITY, 80])[1]
        jpeg = cv2.imdecode(encoded, cv2.IMREAD_COLOR)
        self.assertTrue(detector.is_duplicate(signature(jpeg), jpeg))
        matrix = cv2.getRotationMatrix2D((320, 180), .45, 1)
        matrix[:, 2] += (12, -6)
        shifted = cv2.warpAffine(original, matrix, (640, 360), borderMode=cv2.BORDER_REPLICATE)
        self.assertTrue(detector.is_duplicate(signature(shifted), shifted))
        changed = original.copy()
        cv2.rectangle(changed, (17, 48), (285, 82), (225, 225, 225), -1)
        cv2.putText(changed, 'P1 999 MB', (25, 75), cv2.FONT_HERSHEY_SIMPLEX, .8, (20, 20, 20), 2)
        self.assertFalse(detector.is_duplicate(signature(changed), changed))

    def test_saved_original_and_restart_fingerprint(self):
        parent = PROJECT_ROOT/'work/storage-tests'
        parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=str(parent)) as temporary:
            collector = AutoCollector(temporary)
            collector.detector.stable_seconds = 0
            image = page(1)
            try:
                collector.submit(image)
                end = time.monotonic()+3
                while collector.events.empty() and time.monotonic() < end:
                    collector.submit(image)
                    time.sleep(.05)
                result = collector.events.get(timeout=1)
            finally:
                collector.close()
                collector.thread.join(2)
            original = cv2.imread(str(Path(temporary)/result['image']))
            self.assertTrue(np.array_equal(image, original))
            restarted = AutoCollector(temporary)
            try:
                self.assertIn(signature(image), restarted.detector.known)
                self.assertEqual(len(restarted.list_pages()), 1)
            finally:
                restarted.close()
                restarted.thread.join(2)


if __name__ == '__main__':
    unittest.main()
