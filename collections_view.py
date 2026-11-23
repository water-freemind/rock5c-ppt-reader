"""多页课堂管理；综合摘要来源沿用主界面的本地NPU/DeepSeek选择。"""
import queue
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import cv2
from PIL import Image, ImageTk
from collections_store import CollectionStore
from collection_summary import CollectionSummarizer
from npu_summary import NpuSummarizer
from deepseek_summary import DeepSeekSummarizer
from records import OCR_BACKEND, RecordStore
from ui_layout import FONT, INK, MUTED, ActionButton, repeat_while_open


def choose_classroom(parent, store, suggested_name, on_selected):
    groups = store.list()
    choice = tk.Toplevel(parent)
    choice.title('加入课堂')
    choice.transient(parent.winfo_toplevel())
    choice.resizable(False, False)
    body = tk.Frame(choice, bg='white', padx=22, pady=18)
    body.pack(fill='both', expand=True)
    tk.Label(body, text='选择新建课堂或加入已有课堂', bg='white', fg=INK,
             font=(FONT, 12, 'bold')).pack(anchor='w', pady=(0, 12))
    options = ['新建课堂'] + ['{}（{} 页）'.format(group['name'], len(group['pages'])) for group in groups]
    target = ttk.Combobox(body, state='readonly', values=options, width=42)
    target.current(0)
    target.pack(fill='x')
    title = tk.StringVar(choice, value=suggested_name)
    entry = ttk.Entry(body, textvariable=title)
    entry.pack(fill='x', pady=(9, 2))
    status = tk.StringVar(choice)
    tk.Label(body, textvariable=status, bg='white', fg=MUTED, font=(FONT, 9)).pack(anchor='w')

    def select(event=None):
        entry.configure(state='normal' if target.current() == 0 else 'disabled')

    def submit():
        index = target.current()
        group = groups[index-1] if index > 0 else None
        name = group['name'] if group else title.get().strip()
        if not name:
            status.set('请填写课堂名称。')
            return
        choice.destroy()
        on_selected(name, group['id'] if group else None)

    actions = tk.Frame(body, bg='white')
    actions.pack(anchor='e', pady=(8, 0))
    ActionButton(actions, '取消', choice.destroy, width=78, height=32).pack(side='left', padx=(0, 8))
    ActionButton(actions, '确认加入', submit, primary=True, width=92, height=32).pack(side='left')
    target.bind('<<ComboboxSelected>>', select)
    choice.bind('<Escape>', lambda event: choice.destroy())
    choice.bind('<Return>', lambda event: submit())
    choice.grab_set()
    entry.focus_set()
    entry.selection_range(0, 'end')
    return choice


def show_collections(app, parent, identifier=None):
    store = CollectionStore(app.store.root/'collections', app.store)
    window = tk.Frame(parent, bg='white', highlightbackground='#e3e8f1', highlightthickness=1)
    body = tk.Frame(window, bg='white', padx=24, pady=20)
    body.pack(fill='both', expand=True)
    body.columnconfigure(0, weight=1)
    body.columnconfigure(1, weight=2)
    body.rowconfigure(4, weight=1)
    tk.Label(body, text='课堂名称', bg='white', fg=INK, font=(FONT, 11, 'bold')).grid(row=0, column=0, sticky='w')
    name = tk.StringVar(window, value='我的课堂')
    ttk.Entry(body, textvariable=name, font=(FONT, 12)).grid(row=0, column=1, sticky='ew', padx=(20, 0))
    chooser = ttk.Combobox(body, state='readonly', font=(FONT, 10))
    chooser.grid(row=1, column=0, sticky='ew', pady=12)
    toolbar = tk.Frame(body, bg='white')
    toolbar.grid(row=1, column=1, sticky='e', padx=(20, 0))
    state = dict(current=None, saved=None, collections=[], active=None, generating=False)
    records = app.store.list_records()
    chosen = {r['record_id']: r['active_summary_key'] for r in records}
    tk.Label(body, text='按 Ctrl 或 Shift 选历史页，再调整课堂顺序。', bg='white', fg=MUTED,
             font=(FONT, 9)).grid(row=2, column=0, sticky='w', pady=6)
    source_label = tk.StringVar(window, value='综合摘要使用主界面当前选择的模型。')
    tk.Label(body, textvariable=source_label, bg='white', fg=MUTED, font=(FONT, 9), wraplength=570,
             justify='left').grid(row=2, column=1, sticky='w', padx=(20, 0))
    left = tk.Frame(body, bg='white')
    left.grid(row=4, column=0, sticky='nsew')
    pages = tk.Listbox(left, selectmode='extended', exportselection=False, font=(FONT, 11), height=4,
                       relief='flat', bg='#f7f9fd', fg=INK, selectbackground='#dde5ff')
    pages.pack(fill='both', expand=True)
    for record in records:
        pages.insert('end', record['summary']['title'])
    ordering = tk.Frame(left, bg='white')
    ordering.pack(fill='x', pady=(8, 4))
    tk.Label(ordering, text='课堂顺序', bg='white', fg=INK, font=(FONT, 10, 'bold')).pack(side='left')
    order_box = tk.Listbox(left, exportselection=False, font=(FONT, 10), height=4, relief='flat',
                           bg='#f7f9fd', fg=INK, selectbackground='#dde5ff')
    order_box.pack(fill='both', expand=True)
    ordered = []
    source = ttk.Combobox(body, state='readonly', font=(FONT, 10))
    source.grid(row=5, column=0, sticky='ew', pady=10)
    output = tk.Text(body, wrap='word', font=(FONT, 12), relief='flat', bg='#f7f9fd', fg=INK,
                     padx=16, pady=12, width=1, height=10, state='disabled')
    output.grid(row=4, column=1, sticky='nsew', padx=(20, 0))
    status = tk.StringVar(window, value='综合摘要的 P1、P2 等页码按「课堂顺序」排列。')
    tk.Label(body, textvariable=status, bg='white', fg=MUTED, font=(FONT, 10), wraplength=1000,
             justify='left').grid(row=7, column=0, columnspan=2, sticky='w', pady=10)
    progress = ttk.Progressbar(body, mode='indeterminate')
    progress.grid(row=6, column=0, columnspan=2, sticky='ew')
    result_source = ttk.Combobox(body, state='readonly', font=(FONT, 10))
    result_source.grid(row=5, column=1, sticky='ew', padx=(20, 0), pady=10)
    events = queue.Queue()

    def show_order(index=None):
        selected = order_box.curselection()
        index = selected[0] if index is None and selected else index
        order_box.delete(0, 'end')
        for position, page_index in enumerate(ordered, 1):
            order_box.insert('end', 'P{}  {}'.format(position, records[page_index]['summary']['title']))
        if index is not None and index < len(ordered):
            order_box.selection_set(index)
            order_box.see(index)

    def sync_order():
        selected = pages.curselection()
        order = [i for i in ordered if i in selected]
        order.extend(i for i in selected if i not in order)
        if order != ordered:
            ordered[:] = order
            show_order()

    def move_page(step):
        sync_order()
        selected = order_box.curselection()
        if not selected:
            status.set('请先在「课堂顺序」中选中一页。')
            return
        start = selected[0]
        end = start+step
        if 0 <= end < len(ordered):
            ordered[start], ordered[end] = ordered[end], ordered[start]
            show_order(end)
            status.set('页序已调整，保存课堂后生效。')

    ActionButton(ordering, '下移', lambda: move_page(1), width=56, height=27).pack(side='right')
    ActionButton(ordering, '上移', lambda: move_page(-1), width=56, height=27).pack(side='right', padx=5)

    def refresh_choices():
        state['collections'] = store.list()
        chooser['values'] = [c['name'] for c in state['collections']]
        current = state['current']
        if current:
            for index, collection in enumerate(state['collections']):
                if collection['id'] == current['id']:
                    chooser.current(index)
                    return
        chooser.set('新课堂')

    def page_selected(event=None):
        sync_order()
        selected = pages.curselection()
        if not selected:
            state['active'] = None
            source.configure(values=())
            source.set('')
            source_label.set('综合摘要使用主界面当前选择的模型。')
            return
        index = pages.index('active')
        if index not in selected:
            index = selected[-1]
        record = records[index]
        keys = list(record['summaries'])
        source['values'] = [RecordStore.source_label(record['summaries'][k]) for k in keys]
        source.current(keys.index(chosen[record['record_id']]))
        source_label.set('当前页：'+record['summary']['title']+'；综合摘要使用主界面所选模型。')
        state['active'] = index

    def source_selected(event=None):
        if state['active'] is not None and source.current() >= 0:
            record = records[state['active']]
            chosen[record['record_id']] = list(record['summaries'])[source.current()]

    def order_selected(event=None):
        selected = order_box.curselection()
        if selected:
            page_index = ordered[selected[0]]
            pages.activate(page_index)
            pages.see(page_index)
            page_selected()

    def open_snapshot(saved, label):
        item = next(s for s in saved['sources'] if s['page'] == label)
        record = app.store.load(item['record_id'])
        image = cv2.imread(str(app.store.image_path(record)))
        if image is None:
            status.set('来源原图读取失败')
            return
        viewer = tk.Toplevel(window)
        viewer.title('来源快照 '+label+' · '+item['summary']['title'])
        viewer.geometry('1040x680')
        viewer.configure(bg='white')
        tk.Label(viewer, text=label+' · '+RecordStore.source_label(item['summary'])+' · 生成时快照（只读）',
                 bg='white', fg=INK, font=(FONT, 12, 'bold')).pack(anchor='w', padx=20, pady=16)
        content = tk.Frame(viewer, bg='white')
        content.pack(fill='both', expand=True, padx=20, pady=(0, 20))
        rgb = Image.fromarray(cv2.cvtColor(image, cv2.COLOR_BGR2RGB))
        rgb.thumbnail((540, 570))
        viewer.photo = ImageTk.PhotoImage(rgb)
        tk.Label(content, image=viewer.photo, bg='#f7f9fd').pack(side='left', fill='both', expand=True)
        source_text = tk.Text(content, wrap='word', font=(FONT, 11), width=40, relief='flat', padx=14, pady=10)
        source_text.pack(side='left', fill='both', expand=True)
        source_text.insert('1.0', '原文\n'+item['text']+'\n\n'+app.summary_display(item['summary']))
        source_text.configure(state='disabled')
        return viewer

    def display_result(event=None):
        collection = state['current']
        keys = list(collection.get('results', {})) if collection else []
        output.configure(state='normal')
        output.delete('1.0', 'end')
        if keys and result_source.current() >= 0:
            saved = collection['results'][keys[result_source.current()]]
            state['saved'] = saved
            summary = saved['summary']
            output.insert('end', summary['title']+'\n\n')
            for index, point in enumerate(summary['points']):
                output.insert('end', '• '+point+'\n')
                for label in summary['point_sources'][index]:
                    tag = 'source-{}-{}'.format(index, label)
                    output.insert('end', '打开'+label+'原页  ', tag)
                    output.tag_configure(tag, foreground='#4263eb', underline=True)
                    output.tag_bind(tag, '<Button-1>', lambda event, s=saved, p=label: open_snapshot(s, p))
                output.insert('end', '\n\n')
            if summary.get('quality_warnings'):
                output.insert('end', '需人工复核：\n'+'\n'.join(summary['quality_warnings'])+'\n\n')
            if saved.get('album_subset'):
                output.insert('end', '这份相册摘要覆盖原选的 {} 页。可以生成整个课堂的摘要。\n'.format(len(saved['sources'])))
            elif store.is_stale(collection, saved):
                output.insert('end', '⚠ 原页、来源或页序已改变；这是旧摘要，请重新生成。\n')
        else:
            state['saved'] = None
            output.insert('end', '课堂综合摘要会显示在这里；每条要点可点回来源页。')
        output.configure(state='disabled')

    def show_saved(collection, preferred_key=None):
        state['current'] = collection
        name.set(collection['name'])
        keys = list(collection.get('results', {}))
        result_source['values'] = [
            RecordStore.source_label(collection['results'][key]['summary'])+
            (' · 相册选页' if collection['results'][key].get('album_subset') else '')
            for key in keys]
        if keys:
            result_source.current(keys.index(preferred_key) if preferred_key in keys else len(keys)-1)
        else:
            result_source.set('尚无综合摘要')
        display_result()

    def load_collection(event=None):
        index = chooser.current()
        if index < 0:
            return
        try:
            collection = store.load(state['collections'][index]['id'])
        except FileNotFoundError:
            new_collection()
            refresh_choices()
            status.set('所选课堂已删除，列表已刷新。')
            return
        pages.selection_clear(0, 'end')
        ordered.clear()
        chosen.update((r['record_id'], r['active_summary_key']) for r in records)
        for reference in collection['pages']:
            for index, record in enumerate(records):
                if record['record_id'] == reference['record_id']:
                    pages.selection_set(index)
                    chosen[record['record_id']] = reference['summary_key']
                    ordered.append(index)
        show_order()
        show_saved(collection)
        page_selected()

    def new_collection():
        state['current'] = None
        name.set('我的课堂')
        chooser.set('新课堂')
        pages.selection_clear(0, 'end')
        chosen.update((r['record_id'], r['active_summary_key']) for r in records)
        page_selected()
        result_source.configure(values=())
        result_source.set('尚无综合摘要')
        display_result()
        status.set('填写课堂名称并选择页面后，点击「保存课堂」。')

    def delete_collection():
        current = state['current']
        if not current:
            status.set('请从左侧列表选择要删除的课堂。')
            return
        if state['generating']:
            status.set('正在生成综合摘要，请稍后删除。')
            return
        if not messagebox.askyesno('删除课堂',
                                   '删除「{}」及其综合摘要？\n原始页面、OCR 和单页摘要会保留。'.format(current['name']),
                                   parent=window, default='no'):
            return
        try:
            store.delete(current['id'])
        except OSError as exc:
            status.set('删除失败：'+str(exc))
            return
        new_collection()
        refresh_choices()
        status.set('已删除课堂「{}」。'.format(current['name']))

    def save_collection():
        try:
            sync_order()
            selections = [dict(record_id=records[i]['record_id'], summary_key=chosen[records[i]['record_id']])
                          for i in ordered]
            current = state['current']
            collection = store.save(name.get(), selections, current['id'] if current else None)
            show_saved(collection)
            refresh_choices()
            status.set('课堂已保存，共{}页；可生成综合摘要。'.format(len(selections)))
            return collection
        except (OSError, ValueError, KeyError) as exc:
            status.set('保存失败：'+str(exc))

    def generate():
        if app.busy:
            status.set('主界面正在处理，请稍后重试')
            return
        collection = save_collection()
        if not collection:
            return
        cloud = app.summary_provider.get() == 'DeepSeek API'
        try:
            snapshots = store.snapshots(collection)
            options = dict(progress=events.put, model=app.summary_model.get())
            if cloud:
                backend = DeepSeekSummarizer(app.deepseek_key.get(), **options)
            else:
                if any(page['summary'].get('backend') == OCR_BACKEND for page in snapshots):
                    options['min_collection_points'] = 2
                backend = NpuSummarizer(**options)
        except (OSError, ValueError, KeyError) as exc:
            status.set(str(exc))
            return
        worker = CollectionSummarizer(backend, progress=events.put)
        app.summarizer = worker
        state['generating'] = True
        progress.start(12)
        source_label.set(('DeepSeek API / ' if cloud else '本地NPU / ')+app.summary_model.get())
        def operation():
            try:
                result = worker.summarize(snapshots)
                return dict(collection=store.save_result(collection['id'], snapshots, result), result=result)
            except Exception as exc:
                return dict(error=str(exc))
        def done(value):
            app.status.set('课堂综合摘要生成失败' if 'error' in value else '已保存课堂综合摘要')
            if not window.winfo_exists():
                return
            # 任务可能在两次轮询之间结束，先消费剩余进度，再显示最终结果。
            poll()
            progress.stop()
            state['generating'] = False
            if 'error' in value:
                status.set('综合摘要失败，可重试：'+value['error'])
                return
            if state['current'] and state['current']['id'] == collection['id']:
                show_saved(value['collection'], RecordStore.summary_key(value['result']))
            refresh_choices()
            result = value['result']
            speed = result.get('tokens_per_second')
            status.set('已保存综合摘要 · 总计{:.1f}秒 · {}'.format(result['elapsed_seconds'],
                       '末次生成 {:.2f} token/s'.format(speed) if speed else '未取得token/s统计'))
        app.run(operation, done, input_bound=False)

    def export():
        if not state['current'] or not state['current'].get('results'):
            status.set('请先生成并保存综合摘要')
            return
        path = filedialog.asksaveasfilename(parent=window, defaultextension='.txt')
        if path:
            try:
                store.export(state['current'], path)
                status.set('已导出综合摘要及来源快照')
            except OSError as exc:
                status.set(str(exc))

    for title, callback in [('新建', new_collection), ('保存课堂', save_collection), ('生成综合摘要', generate),
                            ('导出', export), ('删除课堂', delete_collection)]:
        ActionButton(toolbar, title, callback, primary=title == '生成综合摘要', width=105, height=34).pack(side='left', padx=(6, 0))
    pages.bind('<<ListboxSelect>>', page_selected)
    order_box.bind('<<ListboxSelect>>', order_selected)
    source.bind('<<ComboboxSelected>>', source_selected)
    chooser.bind('<<ComboboxSelected>>', load_collection)
    result_source.bind('<<ComboboxSelected>>', display_result)
    def poll():
        if not window.winfo_exists() or app.closed:
            return
        try:
            while True:
                status.set(events.get_nowait()['stage'])
        except queue.Empty:
            pass
    refresh_choices()
    index = next((i for i, group in enumerate(state['collections']) if group['id'] == identifier), None)
    if index is None:
        display_result()
    else:
        chooser.current(index)
        load_collection()
    repeat_while_open(window, 100, poll)
    window.collection_fields = dict(pages=pages, source=source, name=name, save=save_collection, generate=generate,
                                    state=state, output=output, status=status, open_snapshot=open_snapshot,
                                    order=order_box, move=move_page)
    return window
