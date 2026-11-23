"""一页一条历史，选择其中的摘要来源；不重新调用模型。"""
from datetime import datetime
import tkinter as tk
from tkinter import ttk, filedialog
import cv2
from ui_layout import FONT, INK, MUTED, ActionButton
from history_editor import open_history_editor
from records import OCR_BACKEND, RecordStore
from page_evidence import export_pages_evidence
from review_workflow import close_review


def load_into_app(app, record):
    image = cv2.imread(str(app.store.image_path(record)))
    if image is None:
        raise ValueError('历史图片丢失')
    if not app.set_image(image, origin=record['summary'].get('image_input_origin'), captured_at=record['captured_at'],
                         capture_id=record['summary'].get('capture_id')):
        return False
    present_saved_summary(app, record)
    return True


def present_saved_summary(app, record):
    close_review(app)
    app.version += 1
    app.text.delete('1.0', 'end')
    app.text.insert('1.0', record['text'])
    app.text.edit_reset()
    app.text.edit_modified(False)
    app.summary, app.record = record['summary'], record
    app.saved_page = record
    app.ocr_evidence_id = app.summary.get('ocr_evidence_id')
    app.ocr_raw_text = app.summary.get('ocr_raw_text')
    app.review_history = list(app.summary.get('review_history', []))
    app.input_origin = app.summary.get('image_input_origin', app.input_origin)
    app.refresh_saved_summaries()
    app.show_summary(app.summary_display(app.summary))
    if hasattr(app, 'summary_run'):
        ocr_only = app.summary.get('backend') == OCR_BACKEND
        app.summary_run.set('已保存 OCR 原文' if ocr_only else '已保存摘要 · '+RecordStore.source_label(app.summary))
        speed = app.summary.get('tokens_per_second')
        app.summary_metrics.set('' if ocr_only else '原始生成速度 {:.2f} token/s'.format(speed)
                                if speed else '该历史摘要未记录速度')
        app.summary_activity.configure(mode='determinate', value=100)
        app.summary_activity.pack_forget()
    app.restore_draft(record)


def show_history(app, parent):
    window = tk.Frame(parent, bg='white', highlightbackground='#e3e8f1', highlightthickness=1)
    listing = tk.Frame(window, bg='white')
    listing.pack(fill='both', expand=True)
    records = app.store.list_records()
    header = tk.Frame(listing, bg='white')
    header.pack(fill='x', padx=24, pady=(20, 4))
    tk.Label(header, text='搜索标题或 OCR 原文', bg='white', fg=INK, font=(FONT, 11)).pack(side='left')
    query = tk.StringVar(window)
    ttk.Entry(header, textvariable=query).pack(side='left', fill='x', expand=True, padx=(12, 0))
    body = tk.Frame(listing, bg='white')
    body.pack(fill='both', expand=True, padx=24, pady=14)
    box = tk.Listbox(body, font=(FONT, 11), relief='flat', bg='#f7f9fd', fg=INK,
                     highlightthickness=0, selectbackground='#dde5ff', activestyle='none',
                     selectmode='extended', exportselection=False)
    box.pack(side='left', fill='both', expand=True)
    scroll = ttk.Scrollbar(body, command=box.yview)
    scroll.pack(side='right', fill='y')
    box.configure(yscrollcommand=scroll.set)
    def add_row(record):
        timestamp = record['captured_at']
        try:
            timestamp = datetime.fromisoformat(timestamp).astimezone().strftime('%Y-%m-%d %H:%M')
        except ValueError:
            pass
        box.insert('end', '  {} · {} 种来源 · {}'.format(record['summary']['title'], len(record['summaries']), timestamp))
    row = tk.Frame(listing, bg='white')
    row.pack(fill='x', padx=24, pady=(0, 10))
    tk.Label(row, text='记录来源', bg='white', fg=INK, font=(FONT, 10)).pack(side='left', padx=(0, 10))
    source = ttk.Combobox(row, state='readonly', font=(FONT, 11))
    source.pack(side='left', fill='x', expand=True)
    status = tk.StringVar(window, value='选择页面后，可查看 OCR 原文或切换已保存的摘要。')
    tk.Label(listing, textvariable=status, bg='white', fg=MUTED, font=(FONT, 10)).pack(anchor='w', padx=24)
    keys = []
    displayed = []
    def selected(event=None):
        nonlocal keys
        if len(box.curselection()) != 1:
            keys = []
            source.configure(values=())
            source.set('')
            return
        record = displayed[box.curselection()[0]]
        keys = list(record['summaries'])
        source.configure(values=[RecordStore.source_label(record['summaries'][key]) for key in keys])
        source.current(keys.index(record['active_summary_key']) if record['active_summary_key'] in keys else 0)
    box.bind('<<ListboxSelect>>', selected)
    def current():
        if len(box.curselection()) != 1 or source.current() < 0:
            return None
        return RecordStore.select_summary(displayed[box.curselection()[0]], keys[source.current()])

    def load(event=None):
        record = current()
        if record is None:
            status.set('请选择一页打开。多选可以批量导出。')
            return
        try:
            if not load_into_app(app, record):
                return
        except (OSError, ValueError) as exc:
            status.set(str(exc))
            return
        app.show_workspace()
    def saved(record):
        if load_into_app(app, record):
            app.status.set('已更新同页记录中的摘要，历史页数不变')
            app.show_workspace()
    def edit():
        record = current()
        if record is not None:
            def cancel():
                editor.destroy()
                listing.pack(fill='both', expand=True)
            editor = open_history_editor(window, app.store, record, saved, cancel)
            listing.pack_forget()
            editor.pack(fill='both', expand=True)
        else:
            status.set('请选择一页编辑。')

    searchable = [(r, '\n'.join(s.get('title', '')+'\n'+s.get('source_text', r['text'])
                                  for s in r['summaries'].values()).casefold()) for r in records]

    def filter_pages(*args):
        words = query.get().casefold().split()
        displayed[:] = [r for r, text in searchable if all(word in text for word in words)]
        box.delete(0, 'end')
        for record in displayed:
            add_row(record)
        if displayed:
            box.selection_set(0)
        selected()
        status.set('显示 {} / {} 页 · 按 Ctrl 或 Shift 多选后批量导出。'.format(len(displayed), len(records)))

    def export_selected():
        if app.busy:
            status.set('正在处理，请稍后再导出。')
            return
        selection = [displayed[i] for i in box.curselection()]
        if not selection:
            status.set('请先选择要导出的页面。')
            return
        path = filedialog.asksaveasfilename(parent=window, title='批量导出验收材料', defaultextension='.zip',
                                            filetypes=[('验收材料 ZIP', '*.zip')], initialfile='PPT-验收材料.zip')
        if not path:
            return
        def done(count):
            notice = '已导出 {} 页，包含 {} 页的检测材料 · {}'.format(len(selection), count, path)
            app.status.set(notice)
            if window.winfo_exists():
                status.set(notice)
        app.run(lambda: export_pages_evidence(app.store, selection, path), done, input_bound=False)
    actions = tk.Frame(listing, bg='white')
    actions.pack(fill='x', padx=24, pady=(12, 20))
    ActionButton(actions, '打开页面', load, primary=True).pack(side='right')
    ActionButton(actions, '编辑所选摘要', edit).pack(side='right', padx=10)
    ActionButton(actions, '批量导出验收材料', export_selected, width=150).pack(side='left')
    box.bind('<Double-1>', load)
    box.bind('<Return>', load)
    query.trace_add('write', filter_pages)
    filter_pages()
    window.history_fields = dict(pages=box, source=source, load=load, edit=edit, query=query, export=export_selected)
    return window
