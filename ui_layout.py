"""纯Tk桌面布局与圆角按钮；业务逻辑仍由App提供。"""
import tkinter as tk
from tkinter import ttk
from tkinter import font as tkfont

FONT = 'Noto Sans CJK SC'
BG = '#f4f6fa'
INK = '#18243a'
MUTED = '#7a879b'
BLUE = '#4263eb'


def repeat_while_open(widget, interval, callback):
    """Stop Tk polling as soon as its window is destroyed."""
    timer = None
    closed = False

    def tick():
        nonlocal timer
        timer = None
        callback()
        if not closed:
            timer = widget.after(interval, tick)

    def stop(event):
        nonlocal closed
        if event.widget is widget:
            closed = True
            if timer is not None:
                widget.after_cancel(timer)

    widget.bind('<Destroy>', stop, add='+')
    timer = widget.after(interval, tick)


class ActionButton(tk.Canvas):
    """可鼠标及键盘操作的圆角按钮，不增加第三方GUI依赖。"""
    def __init__(self, parent, text, command, primary=False, dark=False, width=130, height=40):
        super().__init__(parent, width=width, height=height, bg=parent.cget('bg'),
                         highlightthickness=0, takefocus=True, cursor='hand2')
        self.text, self.command = text, command
        self.fill = BLUE if primary else ('#1d2b43' if dark else '#edf1f8')
        self.hover = '#3554d4' if primary else ('#2c3b56' if dark else '#e0e7f3')
        self.foreground = '#ffffff' if primary or dark else '#40506c'
        self.over = False
        self.bind('<Configure>', self.paint)
        self.bind('<Enter>', lambda event: self.highlight(True))
        self.bind('<Leave>', lambda event: self.highlight(False))
        self.bind('<Button-1>', self.invoke)
        self.bind('<Return>', self.invoke)
        self.bind('<space>', self.invoke)
        self.bind('<FocusIn>', self.paint)
        self.bind('<FocusOut>', self.paint)

    def show_selected(self, selected):
        self.fill = BLUE if selected else '#1d2b43'
        self.hover = '#3554d4' if selected else '#2c3b56'
        self.paint()

    def highlight(self, value):
        self.over = value
        self.paint()

    def paint(self, event=None):
        self.delete('all')
        w, h, r = self.winfo_width()-2, self.winfo_height()-2, 10
        color = self.hover if self.over else self.fill
        # Tcl 文件选择器的控件没有对应的 Python Widget；直接比较焦点路径。
        outline = '#8da5ff' if str(self.tk.call('focus')) == self._w else color
        self.create_polygon(1+r, 1, w-r, 1, w, 1, w, r+1, w, h-r, w, h,
                            w-r, h, r+1, h, 1, h, 1, h-r, 1, r+1, 1, 1,
                            smooth=True, fill=color, outline=outline, width=2)
        self.create_text((w+1)/2, (h+1)/2, text=self.text, fill=self.foreground,
                         font=(FONT, 10, 'bold'))

    def invoke(self, event=None):
        self.focus_set()
        self.command()
        return 'break'


def label(parent, text, size=10, color=INK, bold=False, **kwargs):
    return tk.Label(parent, text=text, bg=parent.cget('bg'), fg=color,
                    font=(FONT, size, 'bold' if bold else 'normal'), **kwargs)


def build_interface(app):
    root = app.root
    root.title('PPT 阅读助手')
    root.geometry('1480x900')
    root.minsize(1180, 760)
    root.configure(bg=BG)
    tkfont.nametofont('TkDefaultFont').configure(family=FONT, size=10)
    style = ttk.Style(root)
    style.theme_use('clam')
    style.configure('.', font=(FONT, 10))
    style.configure('Quiet.Vertical.TScrollbar', background='#d7dfeb', troughcolor='white',
                    borderwidth=0, arrowsize=7, width=7)
    style.layout('Quiet.Vertical.TScrollbar', [('Vertical.Scrollbar.trough', {
        'sticky': 'ns', 'children': [('Vertical.Scrollbar.thumb', {'expand': 1, 'sticky': 'nswe'})]})])
    style.configure('Activity.Horizontal.TProgressbar', background=BLUE, troughcolor=BG,
                    borderwidth=0, lightcolor=BLUE, darkcolor=BLUE)
    import json
    try:
        saved_host = json.loads((app.store.root/'connection.json').read_text(encoding='utf-8'))['host']
    except (OSError, ValueError, KeyError):
        saved_host = ''
    app.host = tk.StringVar(root, value=saved_host)
    app.device = tk.StringVar(root, value='/dev/video-camera0')
    app.exposure_compensation = tk.BooleanVar(root, value=True)
    app.auto_capture_enabled = tk.BooleanVar(root, value=False)
    app.reading_order = tk.StringVar(root, value='按行')

    sidebar = tk.Frame(root, bg='#111d32', width=192)
    sidebar.pack(side='left', fill='y')
    sidebar.pack_propagate(False)
    brand = tk.Frame(sidebar, bg='#111d32')
    brand.pack(fill='x', padx=22, pady=(26, 28))
    label(brand, 'P', 22, '#98adff', True).pack(anchor='w')
    label(brand, 'PPT 阅读助手', 16, 'white', True).pack(anchor='w', pady=(6, 1))
    label(sidebar, '工 作 空 间', 9, '#7487a7').pack(anchor='w', padx=24, pady=(0, 10))
    app.navigation = {}
    for name, action in [('页面工作台', app.show_workspace), ('历史记录', app.history),
                         ('采集相册', app.capture_album), ('课堂整理', app.collections),
                         ('发送队列', app.pending_transmissions)]:
        button = ActionButton(sidebar, name, action, dark=True, height=36, primary=name == '页面工作台')
        button.pack(fill='x', padx=16, pady=3)
        app.navigation[name] = button
    tk.Frame(sidebar, bg='#293951', height=1).pack(fill='x', padx=24, pady=10)
    for name, action in [('＋  导入图片', app.choose), ('保存当前记录', app.save_from_view), ('加入课堂', app.join_classroom),
                         ('导出笔记', app.export), ('导出验收材料', app.export_evidence),
                         ('发送到电脑', app.transmit)]:
        button = ActionButton(sidebar, name, action, dark=True, height=36)
        button.pack(fill='x', padx=16, pady=3)
        if name == '保存当前记录':
            app.save_button = button
    bottom = tk.Frame(sidebar, bg='#111d32')
    bottom.pack(side='bottom', fill='x', padx=23, pady=24)
    label(bottom, 'ROCK 5C', 10, '#c4d0e5', True).pack(anchor='w')
    label(bottom, '本地识别  /  离线摘要', 9, '#7487a7').pack(anchor='w', pady=(4, 0))

    workspace = tk.Frame(root, bg=BG)
    workspace.pack(side='left', fill='both', expand=True)
    header = tk.Frame(workspace, bg=BG)
    header.pack(fill='x', padx=26, pady=(23, 20))
    title = tk.Frame(header, bg=BG)
    title.pack(side='left')
    app.page_title = tk.StringVar(root, value='页面工作台')
    app.page_hint = tk.StringVar(root, value='导入页面，校对文字，再生成一份清晰笔记。')
    label(title, '', 22, INK, True, textvariable=app.page_title).pack(anchor='w')
    label(title, '', 10, MUTED, textvariable=app.page_hint).pack(anchor='w', pady=(5, 0))
    ActionButton(header, '连接设置', lambda: connection_settings(app), width=100).pack(side='right', padx=(12, 0))
    badge = tk.Frame(header, bg='#e5f4ee', padx=12, pady=7)
    badge.pack(side='right')
    label(badge, '●  生成：本地 NPU', 9, '#31866c').pack()

    footer = tk.Frame(workspace, bg=BG)
    footer.pack(side='bottom', fill='x', padx=26, pady=(8, 14))
    app.status = tk.StringVar(root, value='准备就绪 · 导入图片，或开启相机拍摄页面。')
    tk.Label(footer, textvariable=app.status, bg=BG, fg='#66758d', font=(FONT, 9),
             anchor='w', wraplength=950, justify='left').pack(side='left', fill='x', expand=True)
    app.activity = ttk.Progressbar(footer, mode='indeterminate', length=76,
                                    style='Activity.Horizontal.TProgressbar')
    app.activity.pack(side='right', padx=(12, 0))
    app.activity.pack_forget()

    app.page_area = tk.Frame(workspace, bg=BG)
    app.page_area.pack(fill='both', expand=True, padx=26)
    columns = app.workbench = tk.Frame(app.page_area, bg=BG)
    columns.pack(fill='both', expand=True)
    columns.columnconfigure(0, weight=11, uniform='content')
    columns.columnconfigure(1, weight=10, uniform='content')
    columns.rowconfigure(0, weight=1)
    preview = tk.Frame(columns, bg='white', highlightbackground='#e3e8f1', highlightthickness=1)
    preview.grid(row=0, column=0, sticky='nsew', padx=(0, 16))
    preview_head = tk.Frame(preview, bg='white', padx=18, pady=16)
    preview_head.pack(fill='x')
    label(preview_head, '01', 10, BLUE, True).pack(side='left', padx=(0, 10))
    label(preview_head, '页面预览', 13, INK, True).pack(side='left')
    label(preview_head, '图片 / 相机', 9, MUTED).pack(side='right')
    tools = tk.Frame(preview, bg='white', padx=14, pady=12)
    tools.pack(side='bottom', fill='x')
    for text, command in [('区域校正', app.rectify), ('区域补识', app.ocr_region),
                          ('还原图片', app.reset_image), ('OCR对照', app.compare_ocr)]:
        ActionButton(tools, text, command, width=95, height=34).pack(side='left', padx=(0, 8))
    label(preview, '选四角校正页面，或选两个对角补识漏字。', 9, MUTED,
          anchor='w', padx=18, pady=9).pack(side='bottom', fill='x')
    app.capture_feedback = tk.StringVar(root, value=app.auto_capture_status())
    tk.Label(preview, textvariable=app.capture_feedback, bg='white', fg=MUTED, font=(FONT, 9),
             wraplength=380, justify='left').pack(side='bottom', anchor='w', padx=18)
    tk.Checkbutton(preview, text='自动采集 · 页面稳定 2 秒后保存',
                   variable=app.auto_capture_enabled, command=app.toggle_auto_capture,
                   bg='white', activebackground='white', fg='#66758d', font=(FONT, 9),
                   highlightthickness=0).pack(side='bottom', anchor='w', padx=14)
    camera_controls = tk.Frame(preview, bg='white', padx=14, pady=10)
    camera_controls.pack(side='bottom', fill='x')
    for text, command, primary in [('开启相机', app.camera_start, False),
                                    ('停止', app.stop_preview, False), ('拍照', app.capture, True)]:
        ActionButton(camera_controls, text, command, primary=primary, width=87, height=36).pack(side='left', padx=(0, 7))
    tk.Checkbutton(camera_controls, text='曝光补偿', variable=app.exposure_compensation,
                   bg='white', activebackground='white', fg='#66758d', selectcolor='white',
                   font=(FONT, 9), relief='flat', highlightthickness=0).pack(side='right')
    app.canvas = tk.Canvas(preview, bg='#f7f9fd', width=500, height=420, highlightthickness=0)
    app.canvas.pack(fill='both', expand=True, padx=14)

    right = tk.Frame(columns, bg=BG)
    right.grid(row=0, column=1, sticky='nsew')
    right.rowconfigure(0, weight=5, uniform='notes')
    right.rowconfigure(1, weight=6, uniform='notes')
    right.columnconfigure(0, weight=1)
    for row, number, title, description, button, callback, attr in [
        (0, '02', '识别原文', '文字可以直接修改，摘要将依据修改后的内容生成。', '识别文字', app.ocr, 'text'),
        (1, '03', '智能摘要', '页面标题与 3～5 条要点，方便回顾与整理。', '生成摘要', app.summarize, 'output')]:
        card = tk.Frame(right, bg='white', highlightbackground='#e3e8f1', highlightthickness=1)
        card.grid(row=row, column=0, sticky='nsew', pady=(0, 14) if row == 0 else (0, 0))
        card_head = tk.Frame(card, bg='white', padx=18, pady=10 if row else 14)
        card_head.pack(fill='x')
        label(card_head, number, 10, BLUE, True).pack(side='left', padx=(0, 10))
        label(card_head, title, 13, INK, True).pack(side='left')
        ActionButton(card_head, button, callback, primary=True, width=100, height=34).pack(side='right')
        assistance = ('AI 校对', app.review_ocr) if attr == 'text' else ('知识补充', app.add_knowledge)
        ActionButton(card_head, *assistance, width=84, height=34).pack(side='right', padx=(0, 6))
        if attr == 'text':
            ttk.Combobox(card_head, textvariable=app.reading_order, state='readonly',
                         values=('按行', '先左栏后右栏'), width=12, font=(FONT, 9)).pack(side='right', padx=(8, 8))
        label(card, description, 9, MUTED, anchor='w', padx=18).pack(fill='x')
        text_frame = tk.Frame(card, bg='white')
        text_frame.pack(fill='both', expand=True, padx=(8, 12), pady=(8, 12))
        text = tk.Text(text_frame, height=1, width=1, wrap='char' if row else 'word', font=(FONT, 12), undo=not row,
                       relief='flat', highlightthickness=0, padx=10, pady=7, spacing1=3, spacing3=5,
                       bg='white', fg=INK, insertbackground=BLUE, selectbackground='#dde5ff')
        text.pack(side='left', fill='both', expand=True)
        scroll = ttk.Scrollbar(text_frame, command=text.yview, style='Quiet.Vertical.TScrollbar')
        scroll.pack(side='right', fill='y')
        text.configure(yscrollcommand=scroll.set)
        setattr(app, attr, text)
    app.text.bind('<<Modified>>', app.modified)
    app.output.configure(state='disabled')
    app.display_image = None
    app.canvas.bind('<Configure>', lambda event: redraw_preview(app))
    root.after_idle(lambda: redraw_preview(app))


def redraw_preview(app):
    if app.closed:
        return
    if app.display_image is not None:
        app.render_preview(app.display_image)
        return
    canvas = app.canvas
    canvas.delete('all')
    x, y = canvas.winfo_width()/2, canvas.winfo_height()/2
    canvas.create_rectangle(x-35, y-85, x+35, y+1, fill='white', outline='#cbd6ec', width=2)
    for index, length in enumerate([42, 32, 42, 24]):
        canvas.create_line(x-21, y-61+index*13, x-21+length, y-61+index*13, fill='#c5d1eb', width=3)
    canvas.create_text(x, y+42, text='从一张页面开始', fill='#667ca6', font=(FONT, 15, 'bold'))
    canvas.create_text(x, y+76, text='导入 PPT 图片，或开启相机拍照', fill='#9aa8bf', font=(FONT, 10))


def connection_settings(app):
    dialog = tk.Toplevel(app.root)
    dialog.title('连接设置')
    dialog.configure(bg='white')
    dialog.resizable(False, False)
    dialog.transient(app.root)
    body = tk.Frame(dialog, bg='white', padx=24, pady=20)
    body.pack(fill='both', expand=True)
    for title, variable in [('电脑接收端 IP', app.host), ('摄像头设备节点', app.device)]:
        label(body, title, 10, INK, True).pack(anchor='w', pady=(8, 5))
        ttk.Entry(body, textvariable=variable, width=35, font=(FONT, 11)).pack(fill='x', ipady=5)
    label(body, '摄像头与曝光补偿设置在下次开启预览时生效。', 9, MUTED).pack(anchor='w', pady=(14, 6))
    notice = tk.StringVar(dialog)
    label(body, '', 9, MUTED, textvariable=notice).pack(anchor='w')
    def save_settings():
        from records import atomic_json
        host = app.host.get().strip()
        if not host or len(host) > 253 or any(c.isspace() for c in host):
            notice.set('请输入电脑接收端IP或主机名')
            return
        try:
            atomic_json(app.store.root/'connection.json', dict(host=host))
        except OSError as exc:
            notice.set('保存失败：'+str(exc))
            return
        app.host.set(host)
        dialog.destroy()
    ActionButton(body, '保存设置', save_settings, primary=True).pack(anchor='e', pady=(8, 0))
