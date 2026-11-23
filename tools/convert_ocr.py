"""为 RK3588 转换浮点 PP-OCR 模型；归一化与课程后处理约定一致。"""
import hashlib
import json
import time
from rknn.api import RKNN
from shared.paths import PROJECT_ROOT

root = PROJECT_ROOT
dest = root / 'models'
dest.mkdir(exist_ok=True)
for mode in ('det', 'rec'):
    source = root / ('ppocrv4_' + mode + '.onnx')
    output = dest / ('ppocrv4_' + mode + '_rk3588.rknn')
    toolkit = RKNN(verbose=False)
    started = time.monotonic()
    try:
        kwargs = dict(target_platform='rk3588')
        if mode == 'det':
            kwargs.update(mean_values=[[123.675, 116.28, 103.53]],
                          std_values=[[58.395, 57.12, 57.375]])
        toolkit.config(**kwargs)
        assert toolkit.load_onnx(model=str(source)) == 0
        assert toolkit.build(do_quantization=False) == 0
        assert toolkit.export_rknn(str(output)) == 0
        manifest = dict(target_platform='rk3588', mode=mode, quantization=False,
                        onnx_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
                        rknn_sha256=hashlib.sha256(output.read_bytes()).hexdigest(),
                        elapsed_seconds=time.monotonic()-started)
        output.with_suffix('.json').write_text(json.dumps(manifest, indent=2))
        print('CONVERTED', manifest, flush=True)
    finally:
        toolkit.release()
