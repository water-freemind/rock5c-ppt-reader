"""摄像头独占后台线程；界面仅消费最新帧，不在Tk线程read。"""
import queue
import threading
import cv2
from camera_exposure import ExposureCompensation

_CAPTURE_LOCK = threading.Lock()


class Camera:
    def __init__(self, device, compensate=True):
        self.device = device
        self.frames = queue.Queue(maxsize=1)
        self.stop_event = threading.Event()
        self.error = None
        self.note = ''
        self.compensate = compensate
        self.thread = threading.Thread(target=self._capture, args=(device,), daemon=True)
        self.thread.start()

    def _capture(self, device):
        # 快速关闭再打开时，等上一实例释放驱动并恢复曝光，避免两个线程抢设备。
        with _CAPTURE_LOCK:
            if not self.stop_event.is_set():
                self._capture_exclusive(device)

    def _capture_exclusive(self, device):
        cap = cv2.VideoCapture(device, cv2.CAP_V4L2)
        exposure = None
        try:
            if not cap.isOpened():
                raise RuntimeError('摄像头打开失败，请核对节点和驱动')
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1920)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 1080)
            if self.compensate:
                try:
                    exposure = ExposureCompensation(device)
                except Exception as exc:
                    self.note = '曝光补偿不可用：'+str(exc)
            while not self.stop_event.is_set():
                ok, frame = cap.read()
                if not ok:
                    raise RuntimeError('摄像头读取失败')
                if exposure is not None:
                    try:
                        exposure.update(frame)
                        self.note = exposure.note
                    except Exception as exc:
                        self.note = '曝光补偿失败：'+str(exc)
                        exposure.close()
                        exposure = None
                try:
                    self.frames.get_nowait()
                except queue.Empty:
                    pass
                self.frames.put_nowait(frame)
        except Exception as exc:
            self.error = str(exc)
        finally:
            if exposure is not None:
                try:
                    exposure.close()
                except Exception as exc:
                    self.error = '恢复曝光失败：'+str(exc)
            cap.release()

    def stop(self):
        self.stop_event.set()
