"""私有 RKLLM 子进程摘要；失败时明确报错，不回退到 CPU。"""
from pathlib import Path
import json
import os
import re
import signal
import subprocess
import tempfile
import threading
import time

from summary_client import missing_numeric_facts, unexpected_numeric_facts
from summary_format import parse_summary
from review_format import parse_review, parse_knowledge

LOCAL_MODELS = {
    'Qwen3-4B · 快速 · W8A8': 'Qwen3-4B',
    'Qwen3-8B · 进阶 · W8A8': 'Qwen3-8B',
}
DEFAULT_MODEL = next(iter(LOCAL_MODELS))


def model_file(root, name):
    return root/'models'/f'{name}_RK3588_w8a8.rkllm'


def installed_models(root):
    return (DEFAULT_MODEL,)+tuple(label for label, name in LOCAL_MODELS.items()
                                 if label != DEFAULT_MODEL and model_file(root, name).is_file())


class NpuSummarizer:
    def __init__(self, timeout=180, root=None, progress=None, model=DEFAULT_MODEL, min_collection_points=3):
        self.root = Path(root or Path(__file__).resolve().parent)
        if model not in LOCAL_MODELS:
            raise ValueError('不支持的本地 NPU 模型：'+model)
        self.model = LOCAL_MODELS[model]
        self.timeout = timeout
        self.min_collection_points = min_collection_points
        self._cancelled = threading.Event()
        self._process = None
        self._lock = threading.Lock()
        self.progress = progress or (lambda event: None)

    def cancel(self):
        self._cancelled.set()
        with self._lock:
            process = self._process
            if process is not None and process.poll() is None:
                # 独立进程组保证取消/关闭窗口时释放模型与 NPU 资源。
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass

    def summarize(self, text, task='page'):
        if task not in ('page', 'collection', 'review', 'knowledge'):
            raise ValueError('不支持的摘要任务')
        text = text.strip()
        if not text:
            raise ValueError('OCR原文为空，请先识别或输入文字')
        if len(text) > 1000 or len(text.encode('utf-8')) > 8192:
            raise ValueError('当前NPU模型单页限1000字符，请按页面或段落拆分后处理')
        if self._cancelled.is_set():
            raise RuntimeError('摘要已取消')
        binary = self.root/'experimental/rkllm123/rkllm_probe123'
        library = self.root/'experimental/rkllm123/lib'
        model = model_file(self.root, self.model)
        for path in (binary, library/'librkllmrt.so', model):
            if not path.is_file():
                raise RuntimeError('NPU摘要依赖缺失：'+str(path))
        logs = self.root/'logs/npu-summary'
        logs.mkdir(parents=True, exist_ok=True)
        # 每次保留原始输出、计时及日志，便于实测复核；不改写已有记录。
        folder = Path(tempfile.mkdtemp(prefix='run-', dir=str(logs)))
        source = folder/'source.txt'
        source.write_text(text, encoding='utf-8')
        prefix = folder/'result'
        environment = os.environ.copy()
        environment['LD_LIBRARY_PATH'] = str(library)
        environment['RKLLM_LOG_LEVEL'] = '1'
        # GUI 的 Tk 库预加载只属于 GUI，不传入 C++ 模型子进程。
        environment.pop('LD_PRELOAD', None)
        command = ['prlimit', '--nofile=102400:102400', str(binary), str(model),
                   str(source), str(prefix)]
        if task != 'page':
            command.append(task)
        elif self.model == 'Qwen3-8B':
            command.append('page-coverage')
        started = time.monotonic()
        model_label = self.model+' · 本地NPU'
        self.progress(dict(stage='加载模型', model=model_label))
        with (folder/'runtime.log').open('wb') as log:
            with self._lock:
                if self._cancelled.is_set():
                    raise RuntimeError('摘要已取消')
                process = subprocess.Popen(command, cwd=str(self.root), env=environment,
                                           stdout=log, stderr=subprocess.STDOUT,
                                           start_new_session=True)
                self._process = process
            try:
                try:
                    previous_stage = '加载模型'
                    while True:
                        remaining = self.timeout-(time.monotonic()-started)
                        if remaining <= 0:
                            raise subprocess.TimeoutExpired(command, self.timeout)
                        try:
                            status = process.wait(timeout=min(0.2, remaining))
                            break
                        except subprocess.TimeoutExpired:
                            live_log = (folder/'runtime.log').read_text(encoding='utf-8', errors='replace')
                            stage = ('生成摘要' if 'NPU_FIRST_OUTPUT' in live_log else
                                     '读取原文' if 'NPU_PROBE_RUN_BEGIN' in live_log else '加载模型')
                            if stage != previous_stage:
                                previous_stage = stage
                                self.progress(dict(stage=stage, model=model_label))
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
                    raise TimeoutError('NPU摘要超时，请缩短原文后重试。日志：'+str(folder))
            finally:
                with self._lock:
                    self._process = None
        if self._cancelled.is_set():
            raise RuntimeError('摘要已取消')
        self.progress(dict(stage='校验结果', model=model_label))
        runtime_log = (folder/'runtime.log').read_text(encoding='utf-8', errors='replace')
        if status != 0 or re.search(r'failed|\berror\b', runtime_log, re.I):
            raise RuntimeError('NPU摘要运行失败，未采用输出。日志：'+str(folder))
        raw = prefix.with_name('result_response.txt').read_text(encoding='utf-8')
        metrics = json.loads(prefix.with_name('result_metrics.json').read_text(encoding='utf-8'))
        if task in ('review', 'knowledge'):
            parser = parse_review if task == 'review' else parse_knowledge
            try:
                value = parser(raw, text) if task == 'review' else parser(raw)
            except ValueError as exc:
                raise ValueError('模型输出无法读取，原始结果保存在：'+str(folder)) from exc
            result = dict(model=self.model, source_text=text, values=value,
                          evidence_directory=str(folder))
            (folder/'validated_review.json').write_text(
                json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
            return result
        try:
            result = parse_summary(raw, collection=task == 'collection',
                                   min_points=self.min_collection_points if task == 'collection' else 3)
        except ValueError as exc:
            raise ValueError('NPU模型返回格式错误，请重试。原始结果保存在：'+str(folder)) from exc
        # 仅去除列表标签，不填补、拆分或编写模型的正文内容。
        result['points'] = [re.sub(r'^要点\s*[1-5一二三四五]\s*[:：]\s*', '', p.strip())
                            for p in result['points']]
        if not all(result['points']):
            raise ValueError('NPU模型返回空要点，请重试')
        missing = missing_numeric_facts(text, result)
        unexpected = unexpected_numeric_facts(text, result)
        warnings = []
        if result['output_format_repaired']:
            warnings.append('模型输出格式已自动恢复，请核对摘要内容。')
        if missing:
            warnings.append('摘要可能遗漏数字或日期，请核对：'+'、'.join(missing))
        if unexpected:
            warnings.append('摘要出现原文没有的数字，结果不可直接采用：'+'、'.join(unexpected))
        generated_tokens = metrics.get('generated_tokens', 0)
        generation_ms = metrics.get('sdk_generation_ms', 0)
        speed = generated_tokens/(generation_ms/1000) if generated_tokens > 0 and generation_ms > 0 else None
        result.update(backend='local-rkllm-npu', model=self.model, quantization='W8A8',
                      source_text=text, elapsed_seconds=time.monotonic()-started,
                      first_token_seconds=metrics['init_seconds']+metrics['first_callback_seconds'],
                      generation_seconds=metrics['generation_seconds'],
                      generated_tokens=generated_tokens, tokens_per_second=speed,
                      tokens_per_second_basis='SDK decode',
                      runtime_metrics=metrics, evidence_directory=str(folder),
                      quality_warnings=warnings, missing_numeric_facts=missing,
                      unexpected_numeric_facts=unexpected)
        (folder/'validated_summary.json').write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        return result
