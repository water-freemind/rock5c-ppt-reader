"""在板端构建私有RKLLM启动器；不安装系统组件，不启动推理。"""
from pathlib import Path
import subprocess

base = Path(__file__).resolve().parent
sdk = base/'experimental/rkllm123'
for path in [base/'rkllm_probe123.cpp', sdk/'include/rkllm.h', sdk/'lib/librkllmrt.so']:
    if not path.is_file():
        raise SystemExit('缺少匹配RKLLM 1.2.3依赖：'+str(path))
temporary = sdk/'rkllm_probe123.new'
subprocess.run(['g++', '-std=c++17', '-O2', '-pthread', '-I'+str(sdk/'include'),
                str(base/'rkllm_probe123.cpp'), '-L'+str(sdk/'lib'), '-lrkllmrt', '-o', str(temporary)], check=True)
temporary.replace(sdk/'rkllm_probe123')
print('板端启动器构建完成；这不代表模型推理已验证。')
