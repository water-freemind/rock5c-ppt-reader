"""标准库单元测试，不能作为摄像头或真实LLM测试证据。"""
import json
import socket
import struct
import tempfile
import threading
import unittest
from pathlib import Path
from shared.records import RecordStore
from shared.tcp_transport import receive, send
from board_app.summary.summary_client import LocalSummarizer


class CoreTests(unittest.TestCase):
    def test_persistence_and_stale_summary(self):
        with tempfile.TemporaryDirectory() as folder:
            store = RecordStore(folder)
            text = '内存16GB，验收12月1日'
            summary = dict(title='测试', points=['一', '二', '三'], source_text=text)
            record = store.save(b'test-image-bytes', text, summary)
            recovered = RecordStore(folder).list_records()
            self.assertEqual([record], recovered)
            exported = Path(folder)/'export.txt'
            store.export(record, exported)
            self.assertIn(text, exported.read_text(encoding='utf-8'))
            with self.assertRaises(ValueError):
                store.save(b'bytes', '修改后的原文', summary)

    def test_fragmented_and_concatenated_messages(self):
        listener = socket.socket()
        listener.bind(('127.0.0.1', 0))
        listener.listen(1)
        sent = [dict(record_id=i, text='中文记录'+str(i)+'内容'*100) for i in range(5)]
        data = b''
        for record in sent:
            payload = json.dumps(record, ensure_ascii=False).encode()
            data += struct.pack('!I', len(payload))+payload
        def sender():
            with socket.create_connection(listener.getsockname()) as conn:
                for pos in range(0, len(data), 7):
                    conn.sendall(data[pos:pos+7])
        worker = threading.Thread(target=sender)
        worker.start()
        try:
            conn, _ = listener.accept()
            with conn:
                conn.settimeout(5)
                self.assertEqual(sent, [receive(conn) for _ in range(5)])
        finally:
            worker.join(5)
            listener.close()

    def test_reject_oversized_and_partial_message(self):
        for payload in (struct.pack('!I', 3*1024*1024), struct.pack('!I', 5)+b'ab'):
            a, b = socket.socketpair()
            with a, b:
                a.sendall(payload)
                a.shutdown(socket.SHUT_WR)
                with self.assertRaises((ValueError, ConnectionError)):
                    receive(b)

    def test_local_endpoint_empty_and_unavailable(self):
        with self.assertRaises(ValueError):
            LocalSummarizer('https://example.com')
        with self.assertRaises(ValueError):
            LocalSummarizer().summarize('  ')
        with self.assertRaises(OSError):
            LocalSummarizer('http://127.0.0.1:1', timeout=1).summarize('测试')


if __name__ == '__main__':
    unittest.main(verbosity=2)
