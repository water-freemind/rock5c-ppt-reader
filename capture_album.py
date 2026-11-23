"""采集相册中的多选 OCR、综合摘要和照片管理。"""
import json
import queue
from datetime import datetime, timezone
import tkinter as tk
from tkinter import messagebox, ttk

import cv2
from PIL import Image, ImageTk

from collection_summary import CollectionSummarizer
from collections_store import CollectionStore, digest
from collections_view import choose_classroom
from deepseek_summary import DeepSeekSummarizer
from history_view import load_into_app
from npu_summary import NpuSummarizer
from page_evidence import save_ocr_evidence
from records import OCR_BACKEND, RecordStore, atomic_json, store_lock
from ui_layout import FONT, INK, MUTED, ActionButton, repeat_while_open


def read_json(path, default=None):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        return default


def show_album(app, parent):
    dialog = tk.Frame(parent, bg='white', highlightbackground='#e3e8f1', highlightthickness=1)
    folder = app.store.root/'captures'
    ocr_folder, summary_folder = folder/'batch-ocr', folder/'batch-summaries'
    pages, photos, recognized, errors = {}, {}, {}, {}
    status = tk.StringVar(dialog)
    task_status = tk.StringVar(dialog)
    model_status = tk.StringVar(dialog)
    stages = queue.SimpleQueue()
    processed = {s['capture_id'] for r in app.store.list_records() for s in r['summaries'].values() if s.get('capture_id')}

    tk.Label(dialog, text='按住 Ctrl 或 Shift 多选，也可按 Ctrl+A 全选。综合摘要按采集时间排列。',
             bg='white', fg=MUTED, font=(FONT, 10)).pack(anchor='w', padx=20, pady=(15, 8))
    body = tk.Frame(dialog, bg='white')
    body.pack(fill='both', expand=True, padx=20)
    body.columnconfigure(0, weight=5, uniform='album')
    body.columnconfigure(1, weight=6, uniform='album')
    body.rowconfigure(0, weight=1)
    table = tk.Frame(body, bg='white')
    table.grid(row=0, column=0, sticky='nsew', padx=(0, 12))
    ttk.Style(dialog).configure('Album.Treeview', rowheight=55)
    tree = ttk.Treeview(table, columns=('time', 'state', 'quality'), selectmode='extended',
                        style='Album.Treeview', height=5)
    for column, title, width in [('#0', '预览', 100), ('time', '采集时间', 112),
                                 ('state', '处理状态', 75), ('quality', '清晰度', 58)]:
        tree.heading(column, text=title)
        tree.column(column, width=width, minwidth=width, stretch=column != '#0')
    tree.pack(side='left', fill='both', expand=True)
    scrollbar = ttk.Scrollbar(table, command=tree.yview)
    scrollbar.pack(side='right', fill='y')
    tree.configure(yscrollcommand=scrollbar.set)

    tabs = ttk.Notebook(body)
    tabs.grid(row=0, column=1, sticky='nsew')
    ocr_tab, summary_tab = (tk.Frame(tabs, bg='white') for _ in range(2))
    tabs.add(ocr_tab, text='OCR 原文')
    tabs.add(summary_tab, text='综合摘要')
    chooser = ttk.Combobox(summary_tab, state='readonly')
    chooser.pack(fill='x', padx=8, pady=8)

    def text_panel(parent):
        frame = tk.Frame(parent, bg='white')
        frame.pack(fill='both', expand=True, padx=8, pady=(0, 6))
        text = tk.Text(frame, font=(FONT, 10), bg='#f7f9fd', wrap='word', state='disabled',
                       relief='flat', padx=10, pady=8, width=1, height=6)
        bar = ttk.Scrollbar(frame, command=text.yview)
        text.configure(yscrollcommand=bar.set)
        bar.pack(side='right', fill='y')
        text.pack(side='left', fill='both', expand=True)
        return text

    original, output = text_panel(ocr_tab), text_panel(summary_tab)

    def replace(text, value):
        text.configure(state='normal')
        text.delete('1.0', 'end')
        text.insert('1.0', value)
        text.configure(state='disabled')

    def selection():
        chosen = set(tree.selection())
        return [key for key in tree.get_children() if key in chosen]

    def sources_for(keys):
        if not keys or any(key not in recognized or key in errors for key in keys):
            return []
        return [dict(page='P'+str(index), capture_id=key, text=recognized[key]['text'])
                for index, key in enumerate(keys, 1)]

    def summary_path(sources):
        return summary_folder/(digest(sources)+'.json')

    variants = {}

    def display_summary(event=None):
        index = chooser.current()
        if index < 0:
            replace(output, '请选择照片并完成批量识别，再生成综合摘要。')
            return
        result = list(variants.values())[index]
        text = app.summary_display(result)
        speed = result.get('tokens_per_second')
        text += '\n\n总耗时 {:.1f} 秒'.format(result['elapsed_seconds'])
        if speed is not None:
            text += ' · 末次调用 {:.2f} token/s'.format(speed)
        replace(output, text)

    def show_selection(event=None, prefer=None):
        keys = selection()
        lines = []
        for index, key in enumerate(keys, 1):
            item = recognized.get(key)
            content = errors.get(key) or (item['text'] if item else '尚未批量识别。')
            lines.append('P{} · {}\n{}'.format(index, tree.item(key, 'values')[0], content))
        replace(original, '\n\n'.join(lines) or '选择一张或多张照片，查看或批量识别文字。')
        old_label = chooser.get()
        variants.clear()
        sources = sources_for(keys)
        if sources:
            try:
                variants.update(read_json(summary_path(sources), {}))
            except (OSError, ValueError) as exc:
                task_status.set('综合摘要读取失败：'+str(exc))
        labels = [RecordStore.source_label(value) for value in variants.values()]
        chooser['values'] = labels
        if labels:
            label = prefer or old_label
            chooser.current(labels.index(label) if label in labels else len(labels)-1)
        else:
            chooser.set('尚无选中照片的综合摘要')
        display_summary()

    def refresh():
        changed = False
        try:
            catalog = {value['id']: value for path in folder.glob('*.json')
                       for value in (read_json(path),) if value and not value.get('deleted_at')}
            removed = set(pages)-set(catalog)
            for key in removed:
                tree.delete(key)
                pages.pop(key)
                photos.pop(key, None)
                recognized.pop(key, None)
                errors.pop(key, None)
            changed = bool(removed)
            for index, (key, page) in enumerate(sorted(catalog.items(), key=lambda item: item[1]['captured_at'])):
                cached = read_json(ocr_folder/(key+'.json'))
                if cached and cached != recognized.get(key):
                    recognized[key] = cached
                    changed = True
                phase = ('识别失败' if key in errors else '已保存笔记' if key in processed else
                         '已识别' if key in recognized else '待识别')
                if key in pages:
                    if tree.set(key, 'state') != phase:
                        tree.set(key, 'state', phase)
                    continue
                pages[key] = page
                timestamp = datetime.fromisoformat(page['captured_at'].replace('Z', '+00:00')).astimezone()
                try:
                    with Image.open(folder/page['image']) as image:
                        image.thumbnail((86, 46))
                        photos[key] = ImageTk.PhotoImage(image)
                except OSError:
                    pass
                tree.insert('', index, iid=key, image=photos.get(key, ''),
                            values=(timestamp.strftime('%m-%d %H:%M:%S'), phase, '{:.1f}'.format(page['sharpness'])))
                changed = True
            status.set('{} 张 · 已选 {} 张 · {}'.format(len(pages), len(selection()), app.auto_capture_status()))
            model_status.set('摘要模型  {} / {} · 可在页面工作台切换'.format(
                app.summary_provider.get(), app.summary_model.get()))
            while not stages.empty():
                task_status.set(stages.get_nowait())
            if changed:
                show_selection()
        except (OSError, ValueError, KeyError) as exc:
            task_status.set('相册读取失败：'+str(exc))

    def open_page(key=None):
        keys = selection()
        if key is None and len(keys) != 1:
            task_status.set('请选择一张照片打开。')
            return
        key = key or keys[0]
        page = pages[key]
        if key in processed:
            try:
                for record in app.store.list_records():
                    variants = [variant for variant, source in record['summaries'].items()
                                if source.get('capture_id') == key]
                    if variants:
                        picked = next((variant for variant in variants
                                       if record['summaries'][variant].get('backend') == OCR_BACKEND),
                                      record['active_summary_key'])
                        if load_into_app(app, app.store.select_summary(record, picked)):
                            app.show_workspace()
                        return
            except (OSError, ValueError, KeyError) as exc:
                task_status.set('记录打开失败：'+str(exc))
                return
        image = cv2.imread(str(folder/page['image']))
        if image is None:
            task_status.set('原图读取失败。')
            return
        origin = 'camera:'+page['device'] if page.get('device') else 'camera:auto-capture'
        if app.set_image(image, origin=origin, captured_at=page['captured_at'], capture_id=key):
            item = recognized.get(key)
            if item:
                app.ocr_raw_text = item['text']
                app.ocr_evidence_id = item.get('ocr_evidence_id')
                app.text.insert('1.0', item['text'])
                app.text.edit_modified(False)
                app.text.edit_reset()
            app.status.set('已载入批量识别原文，可校对后生成单页摘要。' if item else '已打开相册照片，可识别文字。')
            app.show_workspace()

    def open_next():
        key = next((key for key in tree.get_children() if key not in processed and key not in recognized), None)
        if key:
            open_page(key)
        else:
            task_status.set('没有待识别的照片。')

    def resume():
        app.auto_capture_enabled.set(True)
        if app.cap is None:
            app.camera_start()
        elif app.collector is None:
            app.toggle_auto_capture()
        app.show_workspace()

    def batch_ocr():
        if app.busy:
            task_status.set('正在处理，请等本次任务完成。')
            return
        keys = selection()
        if not keys:
            task_status.set('请先选择要识别的照片。')
            return
        order = app.ocr_order()
        jobs = [(key, folder/pages[key]['image']) for key in keys
                if key in errors or recognized.get(key, {}).get('reading_order') != order]
        if not jobs:
            task_status.set('所选 {} 张已识别，可直接生成综合摘要。'.format(len(keys)))
            tabs.select(ocr_tab)
            return
        task_status.set('正在准备 NPU 批量识别…')
        tabs.select(ocr_tab)

        def operation():
            failed = {}
            for index, (key, image_path) in enumerate(jobs, 1):
                stages.put('NPU OCR  {}/{}'.format(index, len(jobs)))
                try:
                    image = cv2.imread(str(image_path))
                    if image is None:
                        raise ValueError('原图读取失败')
                    result, annotated = app.recognize_image(image, order)
                    if not result['text'].strip():
                        raise ValueError('未识别出文字')
                    evidence = save_ocr_evidence(app.store.root, image, image, annotated, result, [])
                    ocr_folder.mkdir(parents=True, exist_ok=True)
                    atomic_json(ocr_folder/(key+'.json'),
                                dict(text=result['text'], reading_order=order, ocr_evidence_id=evidence))
                except (OSError, ValueError, RuntimeError, cv2.error) as exc:
                    failed[key] = str(exc)
            return failed

        def done(failed):
            message = '批量识别完成 · {} 张成功 · {} 张失败'.format(len(keys)-len(failed), len(failed))
            app.status.set(message)
            if dialog.winfo_exists():
                for key, _ in jobs:
                    errors.pop(key, None)
                errors.update(failed)
                refresh()
                show_selection()
                task_status.set(message)

        app.run(operation, done, input_bound=False)

    def generate():
        if app.busy:
            task_status.set('正在处理，请等本次任务完成。')
            return
        keys = selection()
        sources = sources_for(keys)
        if not sources:
            task_status.set('请先批量识别所选照片。失败的照片可重试或取消选择。')
            return
        cloud = app.summary_provider.get() == 'DeepSeek API'
        model = app.summary_model.get()
        try:
            backend = (DeepSeekSummarizer(app.deepseek_key.get(), model=model)
                       if cloud else NpuSummarizer(model=model, min_collection_points=2))
        except (OSError, ValueError) as exc:
            task_status.set(str(exc))
            return
        worker = CollectionSummarizer(backend, progress=lambda event: stages.put(event['stage']))
        app.summarizer = worker
        task_status.set('正在生成 {} 页的综合摘要…'.format(len(sources)))
        tabs.select(summary_tab)

        def operation():
            try:
                result = worker.summarize(sources, raw_ocr=True)
            except Exception as exc:
                return None, '综合摘要失败：'+str(exc)
            try:
                summary_folder.mkdir(parents=True, exist_ok=True)
                path = summary_path(sources)
                with store_lock(summary_folder):
                    saved = read_json(path, {})
                    saved[RecordStore.summary_key(result)] = result
                    atomic_json(path, saved)
                return result, '已保存 {} 页综合摘要'.format(len(sources))
            except (OSError, ValueError) as exc:
                return result, '已生成综合摘要，但保存失败：'+str(exc)

        def done(value):
            result, message = value
            app.status.set(message)
            if dialog.winfo_exists():
                refresh()
                if result and selection() == keys:
                    show_selection(prefer=RecordStore.source_label(result))
                    if RecordStore.summary_key(result) not in variants:
                        replace(output, app.summary_display(result))
                task_status.set(message)

        app.run(operation, done, input_bound=False)

    def selected_jobs():
        keys = selection()
        if app.busy:
            task_status.set('正在处理，请等本次任务完成。')
        elif not keys:
            task_status.set('请先选择照片。')
        elif not sources_for(keys):
            task_status.set('请先批量识别所选照片。失败的照片可重试或取消选择。')
        else:
            return [(pages[key], recognized[key]) for key in keys]
        return []

    def persist_pages(jobs):
        references, failed = [], []
        records_by_capture = {source['capture_id']: record for record in app.store.list_records()
                              for source in record['summaries'].values() if source.get('capture_id')}
        for page, item in jobs:
            try:
                original = (folder/page['image']).read_bytes()
                summary = app.store.ocr_source(item['text'])
                summary.update(capture_id=page['id'], ocr_evidence_id=item.get('ocr_evidence_id'),
                               ocr_raw_text=item['text'],
                               image_input_origin='camera:'+(page.get('device') or 'auto-capture'))
                existing = records_by_capture.get(page['id'], {}).get('summaries', {})
                reviewed = existing.get(app.store.summary_key(summary), {})
                if reviewed.get('manually_edited'):
                    if reviewed['source_text'] == summary['source_text']:
                        summary = reviewed
                    else:
                        summary['model'] += '（相册原文）'
                record = app.store.save(original, item['text'], summary, {'captured_at': page['captured_at']},
                                        activate=False)
                references.append(dict(record_id=record['record_id'], summary_key=app.store.summary_key(summary)))
            except (OSError, ValueError, KeyError) as exc:
                failed.append(str(exc))
        return references, failed

    def save_selected():
        jobs = selected_jobs()
        if not jobs:
            return
        task_status.set('正在保存所选照片的 OCR 记录…')

        def done(value):
            references, failed = value
            message = '已保存 {} 条历史记录'.format(len(references))
            if failed:
                message += ' · {} 张失败：{}'.format(len(failed), failed[0])
            app.status.set(message)
            if dialog.winfo_exists():
                for record in app.store.list_records():
                    processed.update(source['capture_id'] for source in record['summaries'].values()
                                     if source.get('capture_id'))
                refresh()
                task_status.set(message)

        app.run(lambda: persist_pages(jobs), done, input_bound=False)

    def join_classroom():
        jobs = selected_jobs()
        if not jobs:
            return
        sources = sources_for(selection())
        try:
            store = CollectionStore(app.store.root/'collections', app.store)
            saved_results = tuple(read_json(summary_path(sources), {}).values())
        except (OSError, ValueError, KeyError) as exc:
            task_status.set('加入课堂失败：'+str(exc))
            return

        def selected(name, identifier):
            task_status.set('正在保存页面并加入课堂…')

            def operation():
                references, failed = persist_pages(jobs)
                if failed:
                    return None, '有 {} 张记录保存失败，请重试：{}'.format(len(failed), failed[0])
                try:
                    group, added = store.merge_pages(name, references, identifier, saved_results)
                except (OSError, ValueError, KeyError) as exc:
                    return None, '加入课堂失败：'+str(exc)
                return group, '「{}」新增 {} 页 · 共 {} 页'.format(group['name'], added, len(group['pages']))

            def done(value):
                group, message = value
                app.status.set(message)
                if dialog.winfo_exists():
                    task_status.set(message)
                    if group:
                        app.collections(group['id'])

            app.run(operation, done, input_bound=False)

        try:
            timestamp = datetime.fromisoformat(jobs[0][0]['captured_at'].replace('Z', '+00:00')).astimezone()
            choose_classroom(dialog, store, '相册 '+timestamp.strftime('%m-%d %H:%M'), selected)
        except (OSError, ValueError, KeyError) as exc:
            task_status.set('无法打开课堂列表：'+str(exc))

    def mark_deleted(keys, deleted):
        completed, failed = [], []
        deleted_at = datetime.now(timezone.utc).isoformat()
        for key in keys:
            path = folder/(key+'.json')
            try:
                page = read_json(path)
                if page is None:
                    raise FileNotFoundError('找不到相册记录')
                if deleted:
                    page['deleted_at'] = deleted_at
                else:
                    page.pop('deleted_at', None)
                atomic_json(path, page)
                completed.append(key)
            except (OSError, ValueError) as exc:
                failed.append(str(exc))
        refresh()
        message = '{} {} 张照片'.format('已删除' if deleted else '已恢复', len(completed))
        if failed:
            message += ' · {} 张失败：{}'.format(len(failed), failed[0])
        task_status.set(message)

    def delete_selected():
        keys = selection()
        if app.busy or not keys:
            task_status.set('请等处理完成后选择要删除的照片。' if app.busy else '请先选择要删除的照片。')
            return
        if messagebox.askyesno('删除照片', '从采集相册删除选中的 {} 张照片？\n可使用「撤销删除」恢复。'.format(len(keys)),
                               parent=dialog, default='no'):
            mark_deleted(keys, True)

    def undo_delete():
        if app.busy:
            task_status.set('请等当前任务完成。')
            return
        try:
            deleted = [value for path in folder.glob('*.json') for value in (read_json(path),)
                       if value and value.get('deleted_at')]
        except (OSError, ValueError) as exc:
            task_status.set('撤销失败：'+str(exc))
            return
        if not deleted:
            task_status.set('当前没有可撤销的删除。')
            return
        latest = max(page['deleted_at'] for page in deleted)
        mark_deleted([page['id'] for page in deleted if page['deleted_at'] == latest], False)

    def select_all(event):
        tree.selection_set(tree.get_children())
        return 'break'

    def select_changed(event):
        show_selection()
        status.set('{} 张 · 已选 {} 张 · {}'.format(len(pages), len(selection()), app.auto_capture_status()))

    footer = tk.Frame(dialog, bg='white')
    footer.pack(fill='x', padx=20, pady=12)
    for variable in (status, model_status, task_status):
        tk.Label(footer, textvariable=variable, bg='white', fg=MUTED, font=(FONT, 9),
                 anchor='w', justify='left', wraplength=700).pack(fill='x', pady=2)
    management = [
        ('继续采集', resume, 88, False), ('下一张待识别', open_next, 116, False),
        ('打开页面', open_page, 88, False), ('删除所选', delete_selected, 88, False),
        ('撤销删除', undo_delete, 88, False)]
    processing = [
        ('批量识别', batch_ocr, 88, False), ('保存所选记录', save_selected, 118, False),
        ('生成综合摘要', generate, 118, False), ('加入课堂', join_classroom, 92, True)]
    for row in (processing, management):
        actions = tk.Frame(footer, bg='white')
        actions.pack(fill='x', pady=(6, 0))
        for label, action, width, primary in row:
            ActionButton(actions, label, action, width=width, height=34, primary=primary).pack(side='left', padx=(0, 7))
    dialog.save_selected = save_selected
    dialog.join_classroom = join_classroom

    def clear_actions(event):
        if event.widget is dialog:
            dialog.save_selected = dialog.join_classroom = None

    dialog.bind('<Destroy>', clear_actions, add='+')
    tree.bind('<<TreeviewSelect>>', select_changed)
    tree.bind('<Control-a>', select_all)
    tree.bind('<Double-1>', lambda event: open_page(tree.identify_row(event.y)) if tree.identify_row(event.y) else None)
    chooser.bind('<<ComboboxSelected>>', display_summary)
    refresh()
    show_selection()
    repeat_while_open(dialog, 300, refresh)
    return dialog
