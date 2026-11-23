"""Offer reviewable corrections in the OCR card and keep study notes with their summary."""
import tkinter as tk
from ui_layout import ActionButton, FONT, INK, MUTED
from npu_summary import DEFAULT_MODEL, NpuSummarizer


def close_review(app):
    if app.review_panel is not None:
        app.review_panel.destroy()
        app.review_panel = None
        app.text.master.pack(fill='both', expand=True, padx=(8, 12), pady=(8, 12))


def review_panel(app, title):
    close_review(app)
    editor = app.text.master
    editor.pack_forget()
    panel = app.review_panel = tk.Frame(editor.master, bg='white')
    panel.pack(fill='both', expand=True, padx=18, pady=(0, 12))
    tk.Label(panel, text=title, bg='white', fg=INK, font=(FONT, 10, 'bold')).pack(anchor='w')
    actions = tk.Frame(panel, bg='white')
    actions.pack(side='bottom', fill='x')
    ActionButton(actions, '返回原文', lambda: close_review(app), width=86, height=29).pack(side='left')
    return panel, actions


def editor_text(app):
    return app.text.get('1.0', 'end-1c')


def apply_edits(text, edits):
    positioned = []
    for edit in edits:
        original = edit['original']
        start = text.find(original)
        if start < 0 or text.find(original, start+1) >= 0:
            raise ValueError('有建议已无法定位，请重新校对')
        positioned.append((start, start+len(original), edit['replacement']))
    right = len(text)
    for start, end, replacement in sorted(positioned, reverse=True):
        if end > right:
            raise ValueError('所选建议有重叠，请只选其中一条')
        text = text[:start]+replacement+text[end:]
        right = start
    return text


def show_edits(app, result, source):
    edits = result['values']
    if not edits:
        app.status.set('当前模型没有找到可定位的校对建议')
        return
    panel, actions = review_panel(app, result['model']+' 的校对建议 · 先对照左侧原图')
    listing = tk.Listbox(panel, selectmode='extended', exportselection=False, height=4, font=(FONT, 10))
    listing.pack(fill='both', expand=True)
    for edit in edits:
        listing.insert('end', edit['original']+' → '+edit['replacement'])
    reason = tk.StringVar(panel, value='按 Ctrl 可选择多条。')
    tk.Label(panel, textvariable=reason, bg='white', fg=MUTED, anchor='w', wraplength=360,
             justify='left', font=(FONT, 9)).pack(fill='x')

    def selected(event=None):
        if listing.curselection():
            reason.set(edits[listing.curselection()[-1]]['reason'])
    listing.bind('<<ListboxSelect>>', selected)

    def accept():
        chosen = [edits[index] for index in listing.curselection()]
        if not chosen:
            app.status.set('请选择要应用的建议')
            return
        if editor_text(app) != source:
            close_review(app)
            app.status.set('原文已变化，请重新校对')
            return
        try:
            revised = apply_edits(source, chosen)
        except ValueError as exc:
            app.status.set(str(exc))
            return
        close_review(app)
        app.text.delete('1.0', 'end')
        app.text.insert('1.0', revised)
        app.modified()
        app.review_history.append(dict(kind='ai-review', model=result['model'], edits=chosen))
        app.status.set('已应用 {} 条校对建议，可继续编辑或生成摘要'.format(len(chosen)))
    ActionButton(actions, '应用所选', accept, primary=True, width=86, height=29).pack(side='right')
    app.status.set('有 {} 条校对建议，确认后才会修改正文'.format(len(edits)))


def show_region(app, value, source, anchor, bounds):
    if not value.strip():
        app.status.set('所选区域未识别出文字，请重新框选或调整图片')
        return
    panel, actions = review_panel(app, '区域 NPU OCR · 确认后插入光标所在行后')
    preview = tk.Text(panel, height=4, wrap='word', font=(FONT, 11))
    preview.pack(fill='both', expand=True)
    preview.insert('1.0', value)
    preview.configure(state='disabled')

    def accept():
        if editor_text(app) != source:
            close_review(app)
            app.status.set('原文已变化，请重新补识')
            return
        close_review(app)
        app.text.insert(anchor, ('\n' if source else '')+value.strip())
        app.modified()
        app.review_history.append(dict(kind='region-ocr', bounds=bounds, text=value))
        app.status.set('区域识别文字已插入，请核对顺序再生成摘要')
    ActionButton(actions, '插入正文', accept, primary=True, width=86, height=29).pack(side='right')
    app.status.set('区域文字已识别，确认后才插入正文')


def generate_assistance(app, task):
    if app.preview_in_use():
        return
    if app.busy:
        app.status.set('正在处理，请稍候')
        return
    source = editor_text(app)
    if not source.strip():
        app.status.set('请先识别或输入文字')
        return
    if task == 'knowledge' and (not app.summary or not app.summary.get('points')
                                or app.summary.get('source_text') != source.strip()):
        app.status.set('请先用当前正文生成摘要，再添加知识补充')
        return
    selected = app.summary_model.get() if app.summary_provider.get() == '本地 NPU' else DEFAULT_MODEL
    backend = app.summarizer = NpuSummarizer(model=selected)
    close_review(app)

    def done(result):
        if editor_text(app) != source:
            app.status.set('正文已变化，请重新处理')
            return
        if task == 'review':
            show_edits(app, result, source)
            return
        app.summary = dict(app.summary, knowledge=dict(model=result['model'], notes=result['values']))
        app.record = None
        app.show_summary(app.summary_display(app.summary))
        if app.saved_page is not None:
            if app.save():
                app.status.set('知识补充已自动保存到当前页')
            else:
                app.status.set('知识补充已生成，自动保存失败：'+app.status.get())
        else:
            app.status.set('知识补充已生成，点击「保存当前记录」即可随笔记导出')

    app.run(lambda: backend.summarize(source, task=task), done)
    app.status.set('正在用本地 NPU {}处理…'.format(backend.model))
