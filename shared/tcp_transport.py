"""4字节网络序长度+UTF-8 JSON；每条记录收到持久化确认后才算发送成功。"""
import json
import socket
import struct

MAX_BYTES = 2*1024*1024


class ReceiverRejected(RuntimeError):
    pass


def connection_message(exc):
    if isinstance(exc, ConnectionRefusedError):
        return '目标未接受连接，请开启 Windows 接收端并核对地址。'
    if isinstance(exc, TimeoutError):
        return '连接超时，请检查网络、接收地址或 SSH 转发。'
    if isinstance(exc, socket.gaierror):
        return '无法识别接收端地址，请检查连接设置。'
    return str(exc)


def receive_exact(conn, count):
    # TCP 一次可能只收到部分数据，读满指定字节数才能解析这段内容。
    result = bytearray()
    while len(result) < count:
        chunk = conn.recv(count-len(result))
        if not chunk:
            raise ConnectionError('连接中断，消息未收完整')
        result.extend(chunk)
    return bytes(result)


def receive(conn):
    size = struct.unpack('!I', receive_exact(conn, 4))[0]
    if not 0 < size <= MAX_BYTES:
        raise ValueError('消息长度超出限制')
    return json.loads(receive_exact(conn, size).decode('utf-8'))


def send(conn, record):
    # 长度按 UTF-8 编码后的字节数计算，接收端据此读完一条完整记录。
    payload = json.dumps(record, ensure_ascii=False).encode('utf-8')
    if not 0 < len(payload) <= MAX_BYTES:
        raise ValueError('消息长度超出限制')
    conn.sendall(struct.pack('!I', len(payload))+payload)


def send_record(host, port, record):
    with socket.create_connection((host, int(port)), timeout=8) as conn:
        conn.settimeout(15)
        send(conn, record)
        ack = receive(conn)
        if ack.get('record_id') == record['record_id'] and ack.get('status') == 'rejected':
            raise ReceiverRejected('Windows 未保存：'+str(ack.get('reason', '接收端拒绝此记录')))
        if ack.get('record_id') != record['record_id'] or ack.get('status') != 'saved':
            raise RuntimeError('未收到接收端保存确认')
        if record.get('revision_id') and ack.get('revision_id') != record['revision_id']:
            raise RuntimeError('接收端未确认当前修订，请更新电脑接收程序后重试')
        return ack


def probe_receiver(host, port):
    with socket.create_connection((host, int(port)), timeout=4) as conn:
        conn.settimeout(4)
        try:
            send(conn, {'type': 'probe'})
            response = receive(conn)
        except ConnectionError as exc:
            raise ConnectionError('未收到接收端就绪确认，请检查 Windows 程序是否启动，或是否需要更新。') from exc
        if response != {'status': 'ready'}:
            raise ConnectionError('目标正在监听，但未返回 Windows 接收端的就绪确认。')
