"""TCP持久发送队列：先落盘再发送，确认修订后移除，重启后继续补发。"""
import copy
import hashlib
import json
from pathlib import Path
import threading
import time
import queue
import uuid
from datetime import datetime, timezone
from records import atomic_json, store_lock
from tcp_transport import send_record, connection_message, MAX_BYTES


class Outbox:
    def __init__(self, folder, sender=send_record):
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        self.sender = sender
        self.events = queue.Queue()
        self.stop_event = threading.Event()
        self.wake = threading.Event()
        self.thread = None
        self.attempt_lock = threading.Lock()

    def _entries(self):
        return [json.loads(path.read_text(encoding='utf-8')) for path in sorted(self.folder.glob('*.json'))]

    def entries(self):
        with store_lock(self.folder):
            return self._entries()

    def confirmations(self):
        result = [json.loads(path.read_text(encoding='utf-8')) for path in (self.folder/'receipts').glob('*.json')]
        return sorted(result, key=lambda r: r['confirmed_at'], reverse=True)[:20]

    @staticmethod
    def _endpoint(host, port):
        host = host.strip()
        port = int(port)
        if not host or len(host) > 253 or any(c.isspace() for c in host) or not 0 < port < 65536:
            raise ValueError('请填写有效的电脑IP和端口')
        return host, port

    @staticmethod
    def _new_entry(host, port, record):
        identifier = hashlib.sha256(json.dumps([host, port, record['record_id']]).encode()).hexdigest()
        return dict(id=identifier, host=host, port=port, record=record, delivery_id=uuid.uuid4().hex,
                    attempts=0, next_attempt=0, last_error='')

    def enqueue(self, host, port, record):
        host, port = self._endpoint(host, port)
        snapshot = copy.deepcopy(record)
        if len(json.dumps(snapshot, ensure_ascii=False).encode('utf-8')) > MAX_BYTES:
            raise ValueError('记录超过TCP消息长度限制，未加入发送队列')
        entry = self._new_entry(host, port, snapshot)
        identifier = entry['id']
        path = self.folder/(identifier+'.json')
        with store_lock(self.folder):
            if path.exists():
                old = json.loads(path.read_text(encoding='utf-8'))
                if old['record'] == snapshot:
                    return identifier
                if snapshot.get('revision', 0) <= old['record'].get('revision', 0):
                    raise ValueError('待发送队列已有更新修订，请重新打开最新历史记录')
            atomic_json(path, entry)
        self.wake.set()
        return identifier

    def retarget(self, identifier, host, port):
        """Move a pending record to a user-selected endpoint and retry immediately."""
        host, port = self._endpoint(host, port)
        with store_lock(self.folder):
            source = next((entry for entry in self._entries() if entry['id'] == identifier), None)
            if source is None:
                raise ValueError('这条任务已发送或已移动，请刷新队列')
            old_path = self.folder/(source['id']+'.json')
            target = self._new_entry(host, port, source['record'])
            new_path = self.folder/(target['id']+'.json')
            if old_path != new_path and new_path.exists():
                existing = json.loads(new_path.read_text(encoding='utf-8'))['record']
                original = source['record']
                if existing != original:
                    old_rev = original.get('revision', 0)
                    new_rev = existing.get('revision', 0)
                    if old_rev == new_rev or original.get('image_sha256') != existing.get('image_sha256'):
                        raise ValueError('新地址已有同编号但内容冲突的任务，原任务已保留')
                    if new_rev > old_rev:
                        target['record'] = existing
            # 先持久化目标任务，断电时最多保留两份，避免尚未确认的记录丢失。
            atomic_json(new_path, target)
            if new_path != old_path:
                old_path.unlink()
        self.wake.set()
        return target['id']

    @staticmethod
    def _same_delivery(current, previous):
        return (current['record'] == previous['record']
                and current.get('delivery_id') == previous.get('delivery_id'))

    def attempt(self, force=False):
        """网络操作在磁盘锁外执行，允许GUI同时加入更新修订。"""
        if not self.attempt_lock.acquire(blocking=False):
            return
        try:
            for entry in self.entries():
                if self.stop_event.is_set():
                    break
                if not force and entry['next_attempt'] > time.time():
                    continue
                path = self.folder/(entry['id']+'.json')
                with store_lock(self.folder):
                    if not path.exists() or not self._same_delivery(
                            json.loads(path.read_text(encoding='utf-8')), entry):
                        continue
                error = ''
                try:
                    self.sender(entry['host'], entry['port'], entry['record'])
                except Exception as exc:
                    error = connection_message(exc)
                with store_lock(self.folder):
                    if not path.exists():
                        continue
                    latest = json.loads(path.read_text(encoding='utf-8'))
                    # 新修订或改址后，旧信道上的 ACK 不能清除新任务。
                    if not self._same_delivery(latest, entry):
                        continue
                    if not error:
                        receipts = self.folder/'receipts'
                        receipts.mkdir(exist_ok=True)
                        receipt = dict(record_id=entry['record']['record_id'],
                                       revision_id=entry['record'].get('revision_id'),
                                       title=entry['record']['summary']['title'], host=entry['host'], port=entry['port'],
                                       confirmed_at=datetime.now(timezone.utc).isoformat())
                        atomic_json(receipts/(entry['id']+'.json'), receipt)
                        path.unlink()
                        self.events.put(dict(kind='confirmed', receipt=receipt))
                    else:
                        latest['attempts'] += 1
                        latest['last_error'] = error
                        latest['next_attempt'] = time.time()+min(300, 5*2**min(latest['attempts']-1, 6))
                        atomic_json(path, latest)
                        self.events.put(dict(kind='retry', error=error))
        finally:
            self.attempt_lock.release()

    def retry(self):
        with store_lock(self.folder):
            for entry in self._entries():
                entry['next_attempt'] = 0
                atomic_json(self.folder/(entry['id']+'.json'), entry)
        self.wake.set()

    def start(self):
        if self.thread is not None:
            return
        def work():
            while not self.stop_event.is_set():
                self.wake.clear()
                self.attempt()
                self.wake.wait(1)
        self.thread = threading.Thread(target=work, name='ppt-tcp-outbox', daemon=True)
        self.thread.start()

    def close(self):
        self.stop_event.set()
        self.wake.set()
