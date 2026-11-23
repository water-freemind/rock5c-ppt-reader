"""查看持久暂存的TCP任务，不阻塞Tk主线程。"""
import tkinter as tk
from tkinter import ttk
from shared.tcp_transport import probe_receiver, connection_message
from board_app.ui.ui_layout import FONT, INK, MUTED, ActionButton, repeat_while_open


def show_outbox(app, parent):
    dialog = tk.Frame(parent, bg='white', highlightbackground='#e3e8f1', highlightthickness=1)
    tk.Label(dialog, text='记录先保存到板子。电脑恢复连接后自动补发，收到保存确认才移出队列。',
             bg='white', fg=MUTED, font=(FONT, 10)).pack(anchor='w', padx=24, pady=(20, 0))
    tree = ttk.Treeview(dialog, columns=('title', 'host', 'attempts', 'error'), show='headings', selectmode='browse')
    for column, title, width in [('title', '页面', 210), ('host', '接收地址', 140),
                                 ('attempts', '尝试次数', 70), ('error', '状态 / 最近错误', 340)]:
        tree.heading(column, text=title)
        tree.column(column, width=width)
    tree.pack(fill='both', expand=True, padx=24, pady=16)
    status = tk.StringVar(dialog)
    notice = tk.StringVar(dialog)
    tk.Label(dialog, textvariable=status, bg='white', fg=MUTED, font=(FONT, 10)).pack(anchor='w', padx=24)
    tk.Label(dialog, textvariable=notice, bg='white', fg=INK, font=(FONT, 10)).pack(anchor='w', padx=24)
    last_rows = None

    def refresh():
        nonlocal last_rows
        if not dialog.winfo_exists():
            return
        try:
            entries = app.outbox.entries()
            receipts = app.outbox.confirmations()
            address = app.host.get().strip()
            rows = [('pending-'+entry['id'], (entry['record']['summary']['title'],
                     '{}:{}'.format(entry['host'], entry['port']), entry['attempts'],
                     '旧地址 · 选中后点「改用当前地址」' if address and entry['host'] != address else
                     entry['last_error'] or '等待电脑确认')) for entry in entries]
            rows += [('receipt-'+str(index), (receipt['title'], '{}:{}'.format(receipt['host'], receipt['port']),
                       '—', '电脑已确认 · '+receipt['record_id'])) for index, receipt in enumerate(receipts)]
            if rows != last_rows:
                selected = tree.selection()
                tree.delete(*tree.get_children())
                for key, values in rows:
                    tree.insert('', 'end', iid=key, values=values)
                if selected and tree.exists(selected[0]):
                    tree.selection_set(selected[0])
                last_rows = rows
            status.set('{} 条待发送 · {} 条最近确认 · 当前接收地址 {}'.format(
                len(entries), len(receipts), app.host.get().strip() or '未设置'))
        except (OSError, ValueError) as exc:
            status.set('读取队列失败：'+str(exc))

    def retarget():
        selected = tree.selection()
        if not selected or not selected[0].startswith('pending-'):
            notice.set('请先选择一条待发送记录')
            return
        address = app.host.get().strip()
        try:
            app.outbox.retarget(selected[0][len('pending-'):], address, 5088)
        except (OSError, ValueError) as exc:
            notice.set(str(exc))
            return
        notice.set('已更新为 {}:5088，正在等待电脑确认'.format(address))
        refresh()

    def test_connection():
        address = app.host.get().strip()
        if not address or app.busy:
            notice.set('请先设置电脑地址，并等待当前任务结束。')
            return
        def operation():
            try:
                probe_receiver(address, 5088)
                return 'Windows 接收端已就绪 · '+address+':5088'
            except (OSError, ValueError) as exc:
                return '连接检查：'+connection_message(exc)
        def done(result):
            app.status.set(result)
            if dialog.winfo_exists():
                notice.set(result)
        notice.set('正在检查 Windows 接收端…')
        app.run(operation, done, input_bound=False)

    buttons = tk.Frame(dialog, bg='white')
    buttons.pack(anchor='e', padx=24, pady=16)
    ActionButton(buttons, '测试连接', test_connection).pack(side='left', padx=(0, 10))
    ActionButton(buttons, '改用当前地址', retarget).pack(side='left', padx=(0, 10))
    ActionButton(buttons, '立即重试', app.outbox.retry, primary=True).pack(side='left')
    refresh()
    repeat_while_open(dialog, 1000, refresh)
    return dialog
