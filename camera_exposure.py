"""IMX219应用层曝光补偿；不修改图像像素、IQ文件或系统服务。"""
from pathlib import Path
import subprocess
import time
import numpy as np


class ExposureCompensation:
    def __init__(self, device):
        self.sensor = None
        self.original = None
        self.note = ''
        self.next_update = time.monotonic()+2
        video = Path('/sys/class/video4linux')/Path(device).resolve().name/'name'
        if not video.exists() or 'rkisp' not in video.read_text():
            return
        candidates = [p for p in Path('/sys/class/video4linux').glob('v4l-subdev*/name')
                      if 'imx219' in p.read_text()]
        if len(candidates) != 1:
            return
        self.sensor = '/dev/'+candidates[0].parent.name
        self.original = self.read()
        self.exposure, self.gain = self.original

    def read(self):
        result = subprocess.run(['v4l2-ctl', '-d', self.sensor, '--get-ctrl=exposure,gain'],
                                check=True, capture_output=True, text=True, timeout=2)
        values = dict(line.split(':', 1) for line in result.stdout.strip().splitlines())
        return int(values['exposure']), int(values['gain'])

    def write(self, exposure, gain):
        subprocess.run(['v4l2-ctl', '-d', self.sensor,
                        '--set-ctrl=exposure='+str(exposure)+',gain='+str(gain)],
                       check=True, capture_output=True, timeout=2)

    def update(self, frame):
        now = time.monotonic()
        if self.sensor is None or now < self.next_update:
            return
        self.next_update = now+0.8
        # 使用稀疏采样的亮度95百分位，避免少量灯珠让整幅图持续欠曝。
        sample = frame[::8, ::8].astype(np.float32)
        luminance = sample[:, :, 0]*0.114+sample[:, :, 1]*0.587+sample[:, :, 2]*0.299
        level = float(np.percentile(luminance, 95))
        ratio = min(3.0, max(0.4, 150/max(level, 1)))
        # 白底 PPT 可以有很亮的区域；只有大面积接近饱和才降低曝光。
        if level >= 115 and np.mean(luminance >= 245) < .35:
            self.note = '当前高光 {:.0f}/255，曝光无需补偿'.format(level)
            return
        product = min(2400*16384, max(16*256, self.exposure*self.gain*ratio))
        # 优先延长曝光，再提高增益；上限约一帧曝光，增益限制64倍以控制噪声。
        exposure = int(min(2400, max(16, product/256)))
        gain = int(min(16384, max(256, product/exposure)))
        if (exposure, gain) != (self.exposure, self.gain):
            self.write(exposure, gain)
            self.exposure, self.gain = exposure, gain
        self.note = '软件曝光补偿 E={} G={}；亮度 {:.0f}/255'.format(exposure, gain, level)
        if product >= 2400*16384 and level < 80:
            self.note += '；光线仍不足，请补光'

    def close(self):
        if self.original is not None and (self.exposure, self.gain) != self.original:
            self.write(*self.original)
