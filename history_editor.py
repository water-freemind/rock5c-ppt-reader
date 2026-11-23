"""历史记录人工校订窗口；提交前验证原文与摘要对应关系。"""
import tkinter as tk
from tkinter import ttk
from records import OCR_BACKEND
from ui_layout import ActionButton, FONT, INK, MUTED


def open_history_editor(parent, store, record, on_saved, on_cancel):
    ocr_only = record['summary'].get('backend') == OCR_BACKEND
    dialog = tk.Frame(parent, bg='white')
    body = tk.Frame(dialog, bg='white', padx=24, pady=20)
    body.pack(fill='both', expand=True)
    body.columnconfigure(0, weight=1)
    body.rowconfigure(3, weight=2)
    if not ocr_only:
        body.rowconfigure(5, weight=1)
    tk.Label(body, text='校订历史笔记', font=(FONT, 20, 'bold'), bg='white', fg=INK).grid(row=0, column=0, sticky='w')
    tk.Label(body, text='校对这一页的 OCR 原文。' if ocr_only else '修改当前来源摘要。原图与旧修订均会保留。',
             font=(FONT, 10), bg='white', fg=MUTED).grid(row=1, column=0, sticky='w', pady=(6, 14))
    title = tk.StringVar(dialog, value=record['summary']['title'])
    ttk.Entry(body, textvariable=title, font=(FONT, 13)).grid(row=2, column=0, sticky='ew', ipady=6)
    source = tk.Text(body, height=8, wrap='word', font=(FONT, 12), padx=10, pady=8)
    source.insert('1.0', record['text'])
    source.grid(row=3, column=0, sticky='nsew', pady=(12, 8))
    points = tk.Text(body, height=6, wrap='word', font=(FONT, 12), padx=10, pady=8)
    points.insert('1.0', '\n'.join(record['summary']['points']))
    reviewed = tk.BooleanVar(dialog, value=False)
    if not ocr_only:
        tk.Label(body, text='摘要要点（每行一条，共3～5条）', font=(FONT, 10), bg='white',
                 fg=INK).grid(row=4, column=0, sticky='w')
        points.grid(row=5, column=0, sticky='nsew', pady=8)
        tk.Checkbutton(body, text='我已核对摘要与修改后的原文一致', variable=reviewed,
                       font=(FONT, 10), bg='white', activebackground='white').grid(row=6, column=0, sticky='w')
    status = tk.StringVar(dialog, value='校对后可保存，也可以在工作台生成摘要。' if ocr_only else
                          '原文改变时，请同步校订摘要并确认；也可取消后在工作台重新生成。')
    tk.Label(body, textvariable=status, wraplength=780, justify='left', bg='white', fg=MUTED,
             font=(FONT, 10)).grid(row=7, column=0, sticky='w', pady=8)
    def commit():
        try:
            result = store.revise(record['record_id'], source.get('1.0', 'end-1c'), title.get(),
                                  points.get('1.0', 'end-1c').splitlines(), reviewed.get(), record.get('active_summary_key'))
        except (OSError, ValueError) as exc:
            status.set(str(exc))
            return
        on_saved(result)
        if dialog.winfo_exists():
            dialog.destroy()
    actions = tk.Frame(body, bg='white')
    actions.grid(row=8, column=0, sticky='e', pady=(8, 0))
    ActionButton(actions, '返回列表', on_cancel).pack(side='left', padx=(0, 10))
    ActionButton(actions, '保存修改', commit, primary=True).pack(side='left')
    dialog.editor_fields = dict(source=source, title=title, points=points, reviewed=reviewed, status=status, commit=commit)
    return dialog
