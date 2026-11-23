"""队列持久化、并发新修订与真实TCP丢ACK恢复；不调用任何模型。"""
import json
from pathlib import Path
import socket
import tempfile
import threading
import unittest
from board_app.outbox import Outbox
from windows_receiver.receiver import persist_record
from shared.tcp_transport import receive, send
from shared.paths import PROJECT_ROOT


def record(revision=1):
    return dict(record_id='a'*32, captured_at='2026-11-13T00:00:00Z', text='中文原文16GB',
                summary=dict(title='TCP测试页', points=['甲', '乙', '丙']),
                revision=revision, revision_id=str(revision)*32, image_sha256='b'*64)


class OutboxTests(unittest.TestCase):
    def setUp(self):
        parent = PROJECT_ROOT/'work/storage-tests'
        parent.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=str(parent))
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_failure_restart_retry_and_coalesce(self):
        def unavailable(*args):
            raise ConnectionError('测试：接收端未启动')
        queue = Outbox(self.root/'queue', sender=unavailable)
        queue.enqueue('127.0.0.1', 5088, record())
        queue.attempt()
        entry = queue.entries()[0]
        self.assertEqual(entry['attempts'], 1)
        self.assertIn('未启动', entry['last_error'])
        self.assertGreater(entry['next_attempt'], 0)
        restarted = Outbox(self.root/'queue', sender=lambda *args: dict(status='saved'))
        self.assertEqual(restarted.entries(), queue.entries())
        restarted.enqueue('127.0.0.1', 5088, record(2))
        self.assertEqual(len(restarted.entries()), 1)
        with self.assertRaises(ValueError):
            restarted.enqueue('127.0.0.1', 5088, record())
        restarted.attempt(force=True)
        self.assertEqual(restarted.entries(), [])
        self.assertEqual(restarted.confirmations()[0]['record_id'], record(2)['record_id'])
        self.assertEqual(Outbox(self.root/'queue').confirmations(), restarted.confirmations())

    def test_old_ack_does_not_remove_new_revision(self):
        queue = Outbox(self.root/'queue')
        def sender(*args):
            queue.enqueue('127.0.0.1', 5088, record(2))
        queue.sender = sender
        queue.enqueue('127.0.0.1', 5088, record())
        queue.attempt()
        self.assertEqual(queue.entries()[0]['record']['revision'], 2)

    def test_retarget_preserves_latest_revision_across_restart(self):
        queue = Outbox(self.root/'queue')
        old = queue.enqueue('old-address', 5088, record())
        fresh = queue.enqueue('new-address', 5088, record(2))
        self.assertEqual(queue.retarget(old, 'new-address', 5088), fresh)
        restarted = Outbox(self.root/'queue')
        self.assertEqual([job['host'] for job in restarted.entries()], ['new-address'])
        self.assertEqual(restarted.entries()[0]['record'], record(2))

    def test_old_ack_after_switching_back_does_not_clear_new_delivery(self):
        queue = Outbox(self.root/'queue')
        original = queue.enqueue('old-address', 5088, record())

        def sender(*args):
            moved = queue.retarget(original, 'new-address', 5088)
            self.assertEqual(queue.retarget(moved, 'old-address', 5088), original)

        queue.sender = sender
        queue.attempt()
        self.assertEqual([job['id'] for job in queue.entries()], [original])
        self.assertEqual(queue.confirmations(), [])

    def test_real_tcp_lost_ack_and_idempotent_resend(self):
        folder = self.root/'received'
        folder.mkdir()
        server = socket.socket()
        server.bind(('127.0.0.1', 0))
        server.listen(2)
        server.settimeout(5)
        host, port = server.getsockname()
        errors = []
        def peer():
            try:
                for index in range(2):
                    conn, _ = server.accept()
                    with conn:
                        conn.settimeout(3)
                        value = receive(conn)
                        persist_record(folder, value)
                        if index:  # 第一次落盘后断开，不返回ACK。
                            send(conn, dict(status='saved', record_id=value['record_id'], revision_id=value['revision_id']))
            except Exception as exc:
                errors.append(exc)
            finally:
                server.close()
        worker = threading.Thread(target=peer)
        worker.start()
        queue = Outbox(self.root/'queue')
        queue.enqueue(host, port, record())
        queue.attempt()
        self.assertEqual(len(queue.entries()), 1)
        queue.attempt(force=True)
        worker.join(6)
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(queue.entries(), [])
        self.assertEqual(queue.events.get_nowait()['kind'], 'retry')
        self.assertEqual(queue.events.get_nowait()['kind'], 'confirmed')
        files = list(folder.glob('*.json'))
        self.assertEqual(len(files), 1)
        self.assertEqual(json.loads(files[0].read_text(encoding='utf-8')), record())

    def test_real_tcp_wrong_revision_ack_is_not_success(self):
        server = socket.socket()
        server.bind(('127.0.0.1', 0))
        server.listen(1)
        server.settimeout(5)
        host, port = server.getsockname()
        def peer():
            conn, _ = server.accept()
            with conn:
                value = receive(conn)
                send(conn, dict(status='saved', record_id=value['record_id'], revision_id='f'*32))
            server.close()
        worker = threading.Thread(target=peer)
        worker.start()
        queue = Outbox(self.root/'queue')
        queue.enqueue(host, port, record())
        queue.attempt()
        worker.join(5)
        self.assertEqual(len(queue.entries()), 1)
        self.assertIn('未确认当前修订', queue.entries()[0]['last_error'])


if __name__ == '__main__':
    unittest.main()
