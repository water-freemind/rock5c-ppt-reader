"""电脑端接收程序，仅依赖Python标准库。"""
import argparse
import json
import os
from pathlib import Path
import re
import socket
import sys
import tempfile
from tcp_transport import receive, send
from records import RecordStore, atomic_json


def persist_record(folder, record):
    folder = Path(folder)
    identifier = record.get('record_id', '')
    if not re.fullmatch('[a-f0-9]{32}', identifier):
        raise ValueError('记录编号不合法')
    if not all(key in record for key in ('captured_at', 'text', 'summary')):
        raise ValueError('缺少记录字段')
    target = folder/(identifier+'.json')
    if target.exists():
        existing = json.loads(target.read_text(encoding='utf-8'))
        if existing == record:
            return
        revision = record.get('revision', 0)
        if (not isinstance(revision, int) or isinstance(revision, bool)
                or revision <= existing.get('revision', 0)
                or not re.fullmatch('[a-f0-9]{32}', record.get('revision_id', ''))
                or not re.fullmatch('[a-f0-9]{64}', record.get('image_sha256', ''))
                or (existing.get('image_sha256') and existing['image_sha256'] != record['image_sha256'])):
            raise ValueError('同页修订冲突或版本过旧。请发送新修订，或在 Windows 选另一个目录。')
        archive = folder/'.versions'/identifier
        archive.mkdir(parents=True, exist_ok=True)
        import hashlib
        digest = hashlib.sha256(json.dumps(existing, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        if not (archive/(digest+'.json')).exists():
            atomic_json(archive/(digest+'.json'), existing)
    atomic_json(target, record)


def choose_output_folder(initial):
    import tkinter as tk
    from tkinter import filedialog

    folder = Path(initial).expanduser().resolve()
    root = tk.Tk()
    root.withdraw()
    root.attributes('-topmost', True)
    try:
        return filedialog.askdirectory(parent=root, title='选择 Windows 接收文件夹',
                                      initialdir=str(folder if folder.is_dir() else folder.parent),
                                      mustexist=True)
    finally:
        root.destroy()


def write_markdown(folder, record):
    destination = folder/(record['record_id']+'.md')
    with tempfile.NamedTemporaryFile(dir=str(folder), suffix='.md', delete=False) as stream:
        temporary = Path(stream.name)
    try:
        RecordStore.export_markdown(record, temporary)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def serve(host, port, folder, *, stop=None, notify=None, ready=None):
    folder = Path(folder).expanduser().resolve()
    folder.mkdir(parents=True, exist_ok=True)
    log = notify or (lambda text: print(text, flush=True))
    with socket.socket() as server:
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind((host, port))
        server.listen(5)
        if stop is not None:
            server.settimeout(.5)
        log('接收端监听 '+str(server.getsockname()))
        log('保存文件夹： '+str(folder))
        if ready is not None:
            ready()
        while stop is None or not stop.is_set():
            try:
                conn, address = server.accept()
            except socket.timeout:
                continue
            with conn:
                conn.settimeout(15)
                try:
                    while stop is None or not stop.is_set():
                        record = receive(conn)
                        if record == {'type': 'probe'}:
                            send(conn, {'status': 'ready'})
                            continue
                        if not isinstance(record, dict):
                            raise ValueError('记录内容必须是 JSON 对象')
                        try:
                            persist_record(folder, record)
                        except (OSError, ValueError, TypeError) as exc:
                            reason = '无法写入，检查 Windows 文件夹权限和空间。' if isinstance(exc, OSError) else str(exc)
                            send(conn, dict(record_id=record.get('record_id'), status='rejected', reason=reason))
                            log('记录未保存 · '+reason)
                            continue
                        identifier = record['record_id']
                        try:
                            write_markdown(folder, record)
                        except (OSError, KeyError, ValueError, TypeError) as exc:
                            log('JSON 已保存，Markdown 导出失败 · '+str(exc))
                        ack = dict(record_id=identifier, status='saved')
                        if record.get('revision_id'):
                            ack['revision_id'] = record['revision_id']
                        send(conn, ack)
                        log('已保存 {} · 修订 {} · {} 种摘要'.format(identifier, record.get('revision', 1),
                            len(record.get('summaries', {'legacy': record['summary']}))))
                except ConnectionError:
                    pass
                except (OSError, ValueError, TypeError) as exc:
                    log('连接处理失败 {} · {}'.format(address, exc))


if __name__ == '__main__':
    # 重定向到Windows终端/日志时也使用UTF-8，避免中文被按系统代码页误解码。
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser()
    parser.add_argument('--host', default='0.0.0.0')
    parser.add_argument('--port', type=int, default=5088)
    parser.add_argument('--output', default='received')
    parser.add_argument('--choose-folder', action='store_true', help='启动时选择接收文件夹')
    parser.add_argument('--gui', action='store_true', help='打开 Windows 接收窗口')
    parser.add_argument('--start', action='store_true', help='打开接收窗口后立即开始监听')
    args = parser.parse_args()
    if args.gui:
        from receiver_window import show_receiver
        show_receiver(serve, args.host, args.port, args.output, args.start)
    else:
        output = choose_output_folder(args.output) if args.choose_folder else args.output
        if output:
            serve(args.host, args.port, output)
