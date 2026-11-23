"""Windows 接收端界面。接收线程只发送状态事件。"""
import os
from pathlib import Path
import queue
import threading
import tkinter as tk
from tkinter import filedialog, ttk


def show_receiver(serve, host, port, initial, auto_start=False):
    root = tk.Tk()
    root.title('PPT 阅读助手 · Windows 接收端')
    root.minsize(630, 350)
    body = ttk.Frame(root, padding=18)
    body.pack(fill='both', expand=True)
    ttk.Label(body, text='Windows 接收端', font=('Microsoft YaHei UI', 16, 'bold')).pack(anchor='w')
    ttk.Label(body, text='监听地址 {}:{} · 每条记录保存为 JSON 和 Markdown'.format(host, port)).pack(anchor='w', pady=(4, 14))
    folder = tk.StringVar(root, value=str(Path(initial).expanduser().resolve()))
    status = tk.StringVar(root, value='选择目录后开始接收。')
    controls = ttk.Frame(body)
    controls.pack(fill='x')
    ttk.Entry(controls, textvariable=folder, state='readonly').pack(side='left', fill='x', expand=True)
    actions = ttk.Frame(body)
    actions.pack(fill='x', pady=9)
    ttk.Label(body, textvariable=status, wraplength=580).pack(anchor='w', pady=(0, 8))
    log = tk.Text(body, height=8, wrap='word', state='disabled', font=('Microsoft YaHei UI', 10))
    log.pack(fill='both', expand=True)
    events = queue.Queue()
    worker_stop = None

    def browse():
        current = Path(folder.get())
        selection = filedialog.askdirectory(parent=root, title='选择 Windows 接收文件夹',
                                            initialdir=str(current if current.is_dir() else current.parent), mustexist=True)
        if selection:
            folder.set(selection)

    def open_folder():
        try:
            selected = Path(folder.get())
            selected.mkdir(parents=True, exist_ok=True)
            os.startfile(str(selected))
        except (OSError, AttributeError) as exc:
            status.set('无法打开接收目录：'+str(exc))

    def start():
        nonlocal worker_stop
        if worker_stop is not None:
            return
        path = folder.get()
        worker_stop = stop = threading.Event()
        choose.configure(state='disabled')
        start_button.configure(state='disabled')
        stop_button.configure(state='normal')
        status.set('正在启动…')

        def work():
            try:
                serve(host, port, path, stop=stop, notify=lambda message: events.put(('log', message)),
                      ready=lambda: events.put(('ready', '正在接收 · '+path)))
            except OSError as exc:
                events.put(('error', '启动或运行失败：'+str(exc)))
            finally:
                events.put(('stopped', '接收已停止。'))

        threading.Thread(target=work, daemon=True).start()

    def stop():
        if worker_stop is not None:
            worker_stop.set()
            stop_button.configure(state='disabled')
            status.set('正在停止，等待当前连接结束…')

    def poll():
        nonlocal worker_stop
        try:
            while True:
                kind, message = events.get_nowait()
                if kind == 'log':
                    log.configure(state='normal')
                    log.insert('end', message+'\n')
                    if int(log.index('end-1c').split('.')[0]) > 300:
                        log.delete('1.0', '2.0')
                    log.see('end')
                    log.configure(state='disabled')
                elif kind == 'stopped':
                    worker_stop = None
                    choose.configure(state='normal')
                    start_button.configure(state='normal')
                    stop_button.configure(state='disabled')
                    if not status.get().startswith('启动或运行失败'):
                        status.set(message)
                else:
                    status.set(message)
        except queue.Empty:
            pass
        root.after(100, poll)

    choose = ttk.Button(controls, text='选择文件夹', command=browse)
    choose.pack(side='left', padx=(8, 0))
    start_button = ttk.Button(actions, text='开始接收', command=start)
    start_button.pack(side='left')
    stop_button = ttk.Button(actions, text='停止接收', command=stop, state='disabled')
    stop_button.pack(side='left', padx=8)
    ttk.Button(actions, text='打开接收文件夹', command=open_folder).pack(side='left')
    root.after(100, poll)
    if auto_start:
        root.after_idle(start)
    root.mainloop()
