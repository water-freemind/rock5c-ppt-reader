"""同图OCR对照窗口，左右结果只读；人工参考可计算CER，未填写不显示准确率。"""
import tkinter as tk
import json
from tkinter import filedialog
from PIL import Image, ImageTk
from ocr_comparison import save_reference, export_comparison
from ui_layout import FONT, INK, MUTED, ActionButton


def show_comparison(app, result):
    folder = app.store.root/'comparisons'
    window = tk.Toplevel(app.root)
    window.title('同图 OCR 对照')
    window.geometry('1120x800')
    window.minsize(940, 680)
    window.configure(bg='white')
    window.transient(app.root)
    body = tk.Frame(window, bg='white', padx=20, pady=16)
    body.pack(fill='both', expand=True)
    for column in (0, 1):
        body.columnconfigure(column, weight=1, uniform='compare')
    body.rowconfigure(2, weight=1)
    body.rowconfigure(3, weight=1)
    window.photos = []
    for column, key, title in [(0, 'original', '校正前'), (1, 'corrected', '校正后')]:
        value, ocr = result[key], result[key]['ocr']
        tk.Label(body, text='{} · {:.3f}秒 · {}行'.format(title, ocr['elapsed_seconds'], len(ocr['lines'])),
                 bg='white', fg=INK, font=(FONT, 13, 'bold')).grid(row=0, column=column, sticky='w', pady=(0, 8))
        picture = Image.open(folder/result['id']/value['annotated'])
        picture.thumbnail((490, 270))
        photo = ImageTk.PhotoImage(picture)
        window.photos.append(photo)
        tk.Label(body, image=photo, bg='#f7f9fd').grid(row=2, column=column, sticky='nsew', padx=(0, 8) if not column else (8, 0))
        text = tk.Text(body, wrap='word', width=1, height=7, font=(FONT, 11), relief='flat', padx=12, pady=10)
        text.grid(row=3, column=column, sticky='nsew', padx=(0, 8) if not column else (8, 0), pady=10)
        text.insert('1.0', ocr['text'] or '未检测到可识别文字')
        text.configure(state='disabled')
    tk.Label(body, text='人工参考原文（可选；请按本次目标PPT区域核对，忽略标点和大小写）',
             bg='white', fg=MUTED, font=(FONT, 10)).grid(row=4, column=0, columnspan=2, sticky='w')
    reference = tk.Text(body, height=3, wrap='word', font=(FONT, 10))
    reference.grid(row=5, column=0, columnspan=2, sticky='ew', pady=8)
    notice = tk.StringVar(window, value='结果已保存，可稍后从同一图片和校正状态再次打开。字符更多或置信度更高不代表识别更准确。')
    tk.Label(body, textvariable=notice, bg='white', fg=MUTED, font=(FONT, 10), wraplength=1030,
             justify='left').grid(row=6, column=0, columnspan=2, sticky='w', pady=(0, 8))
    def calculate():
        if app.busy:
            notice.set('正在处理，请稍后计算')
            return
        value = reference.get('1.0', 'end-1c')
        def operation():
            try:
                return save_reference(folder, result, value)
            except (OSError, ValueError) as exc:
                return dict(error=str(exc))
        def done(metrics):
            if not window.winfo_exists():
                return
            if 'error' in metrics:
                notice.set(metrics['error'])
                return
            notice.set('归一化字符错误率CER：校正前{:.2%}；校正后{:.2%}。越低越好；仅针对这张图及输入的人工参考。'.format(
                metrics['original_cer'], metrics['corrected_cer']))
        app.run(operation, done, input_bound=False)
    reference_path = folder/result['id']/'reference.json'
    if reference_path.exists():
        try:
            saved = json.loads(reference_path.read_text(encoding='utf-8'))
            reference.insert('1.0', saved['reference'])
            notice.set('已保存CER：校正前{:.2%}；校正后{:.2%}。人工参考尚未独立复核。'.format(saved['original_cer'], saved['corrected_cer']))
        except (OSError, ValueError, KeyError) as exc:
            notice.set('参考记录读取失败：'+str(exc))
    def export():
        path = filedialog.asksaveasfilename(parent=window, defaultextension='.txt')
        if path:
            try:
                saved = json.loads(reference_path.read_text(encoding='utf-8')) if reference_path.exists() else None
                export_comparison(result, path, saved)
                notice.set('已导出两份OCR原文和耗时')
            except OSError as exc:
                notice.set(str(exc))
    actions = tk.Frame(body, bg='white')
    actions.grid(row=7, column=0, columnspan=2, sticky='e')
    ActionButton(actions, '计算CER', calculate, width=110).pack(side='left', padx=8)
    ActionButton(actions, '导出对照', export, width=110, primary=True).pack(side='left')
    window.comparison_fields = dict(reference=reference, calculate=calculate, notice=notice)
    return window
