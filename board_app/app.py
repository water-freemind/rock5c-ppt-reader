"""板端Tk界面：耗时任务在单工作线程执行，Tk仅由主线程更新。"""
from pathlib import Path
import concurrent.futures
import hashlib
import io
import json
import queue
import subprocess
import time
from datetime import datetime, timezone
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from board_app.ui.ui_layout import build_interface, redraw_preview, repeat_while_open
import cv2
from PIL import Image, ImageTk
from board_app.ocr.ocr_engine import OCREngine, crop_line
from board_app.ui.summary_controls import setup_summary_controls, start_summary
from shared.records import OCR_BACKEND, RecordStore, atomic_json
from board_app.ui.history_view import show_history, present_saved_summary
from board_app.outbox import Outbox
from board_app.ui.outbox_view import show_outbox
from board_app.capture.auto_capture import AutoCollector
from board_app.ui.capture_album import show_album
from board_app.storage.collections_store import CollectionStore
from board_app.ui.collections_view import choose_classroom, show_collections
from board_app.ocr.ocr_comparison import compare, find_comparison, image_digest
from board_app.ui.comparison_view import show_comparison
from board_app.storage.page_evidence import save_ocr_evidence
from board_app.capture.camera import Camera
from board_app.ocr.image_processing import rectify_page
from board_app.ui.review_workflow import close_review, editor_text, generate_assistance, show_region
from shared.paths import PROJECT_ROOT

ROOT = PROJECT_ROOT


class App:
    def __init__(self, root):
        self.root = root
        self.pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        self.events = queue.Queue()
        self.engine = None
        self.image = None
        self.raw_image = None
        self.summary = None
        self.record = None
        self.saved_page = None
        self.busy = False
        self.closed = False
        self.version = 0
        self.cap = None
        self.collector = None
        self.last_collector = None
        self.capture_problem = ''
        self.frame = None
        self.page_preview = None
        self.camera_workers_stopping = []
        self.corners = []
        self.image_operations = []
        self.capture_time = None
        self.capture_id = None
        self.input_origin = None
        self.ocr_evidence_id = None
        self.ocr_raw_text = None
        self.review_history = []
        self.review_panel = None
        self.summarizer = None
        self.feature_view = None
        self.store = RecordStore(ROOT/'data')
        self.outbox = Outbox(ROOT/'data/outbox')
        build_interface(self)
        setup_summary_controls(self)
        self.outbox.start()
        self.canvas.bind('<Button-1>', self.click_corner)
        root.protocol('WM_DELETE_WINDOW', self.request_close)
        repeat_while_open(root, 80, self.poll)

    def modified(self, event=None):
        if self.text.edit_modified():
            self.version += 1
            self.summary = None
            self.record = None
            self.refresh_saved_summaries()
            self.show_summary('原文已变更，摘要待重新生成。')
            self.text.edit_modified(False)

    def show_summary(self, value):
        self.output.config(state='normal')
        self.output.delete('1.0', 'end')
        self.output.insert('1.0', value)
        self.output.config(state='disabled')

    @staticmethod
    def summary_display(summary):
        if summary.get('backend') == OCR_BACKEND:
            return summary['title']+'\n\nOCR 原文已保存，可以继续生成摘要。'
        value = summary['title']+'\n'+'\n'.join('• '+p for p in summary['points'])
        if summary.get('quality_warnings'):
            value += '\n\n需人工复核：\n'+'\n'.join(summary['quality_warnings'])
        if summary.get('knowledge'):
            knowledge = summary['knowledge']
            value += '\n\n知识补充 · '+knowledge['model']+' 生成\n'+'\n'.join('• '+p for p in knowledge['notes'])
        return value

    def run(self, operation, callback, input_bound=True):
        if self.busy:
            self.status.set('正在处理，请稍候')
            return
        self.busy = True
        self.activity.pack(side='right', padx=(12, 0))
        self.activity.start(12)
        self.status.set('正在处理，窗口可继续操作…')
        # 记下启动任务时的输入版本，防止旧页面的结果覆盖新页面。
        version = self.version if input_bound else None
        def work():
            try:
                self.events.put((callback, version, operation(), None))
            except Exception as exc:
                self.events.put((callback, version, None, str(exc)))
        self.pool.submit(work)

    def poll(self):
        if self.closed:
            return
        # 后台线程把结果放进队列，由界面线程取出并更新窗口。
        try:
            while True:
                callback, version, result, error = self.events.get_nowait()
                self.busy = False
                self.activity.stop()
                self.activity.pack_forget()
                if error:
                    self.status.set('失败，可重试：'+error)
                elif version is not None and version != self.version:
                    self.status.set('输入已变化，本次旧结果已丢弃，请重新处理')
                else:
                    callback(result)
        except queue.Empty:
            pass
        try:
            while True:
                event = self.outbox.events.get_nowait()
                if not self.busy:
                    if event['kind'] == 'confirmed':
                        receipt = event['receipt']
                        self.status.set('电脑已确认保存：'+receipt['title']+' · '+receipt['record_id'])
                    else:
                        self.status.set('发送未成功，记录已暂存并会自动重试；请查看发送队列')
        except queue.Empty:
            pass
        self.preview()
        capture_status = self.auto_capture_status()
        if capture_status != self.capture_feedback.get():
            self.capture_feedback.set(capture_status)

    def draw(self, image):
        self.page_preview = image
        if self.cap is None:
            self.render_preview(image)

    def render_preview(self, image):
        self.display_image = image
        rgb = Image.fromarray(cv2.cvtColor(image, cv2.COLOR_BGR2RGB))
        width, height = max(200, self.canvas.winfo_width()), max(200, self.canvas.winfo_height())
        self.scale = min(width/rgb.width, height/rgb.height)
        rgb = rgb.resize((max(1, int(rgb.width*self.scale)), max(1, int(rgb.height*self.scale))))
        self.photo = ImageTk.PhotoImage(rgb)
        self.image_offset = ((width-rgb.width)//2, (height-rgb.height)//2)
        self.canvas.delete('all')
        self.canvas.create_image(*self.image_offset, anchor='nw', image=self.photo)
        if self.cap is None:
            for x, y in self.corners:
                self.mark_corner(self.image_offset[0]+x*self.scale, self.image_offset[1]+y*self.scale)

    def draft_path(self, record):
        source = hashlib.sha256(record['active_summary_key'].encode('utf-8')).hexdigest()
        return self.store.root/'drafts'/record['record_id']/(source+'.json')

    def preserve_page(self):
        if self.image is None:
            return True
        text = editor_text(self)
        if self.saved_page is None:
            return not (text.strip() or self.summary) or messagebox.askokcancel(
                '尚未保存当前页', '继续会丢弃这张新页面上未保存的内容。要继续吗？', parent=self.root,
                default=messagebox.CANCEL)
        path = self.draft_path(self.saved_page)
        try:
            if text.strip() == self.saved_page['text']:
                if self.record is None:
                    path.unlink(missing_ok=True)
                return True
            path.parent.mkdir(parents=True, exist_ok=True)
            atomic_json(path, dict(text=text, evidence_id=self.ocr_evidence_id,
                                   raw=self.ocr_raw_text, review_history=self.review_history))
            return True
        except OSError as exc:
            self.status.set('草稿保存失败，当前页面已保留：'+str(exc))
            return False

    def restore_draft(self, record):
        try:
            draft = json.loads(self.draft_path(record).read_text(encoding='utf-8'))
        except FileNotFoundError:
            return
        except (OSError, ValueError) as exc:
            self.status.set('草稿读取失败：'+str(exc))
            return
        if draft['text'].strip() == record['text']:
            return
        self.text.delete('1.0', 'end')
        self.text.insert('1.0', draft['text'])
        self.modified()
        self.text.edit_reset()
        self.ocr_evidence_id = draft.get('evidence_id')
        self.ocr_raw_text = draft.get('raw')
        self.review_history = draft.get('review_history', [])
        self.summary_run.set('已恢复 OCR 校对草稿 · 可继续编辑或生成摘要')

    def set_image(self, image, origin=None, captured_at=None, capture_id=None):
        if not self.preserve_page():
            return False
        self.camera_stop()
        close_review(self)
        self.image = image.copy()
        self.raw_image = image.copy()
        self.image_operations = []
        self.version += 1
        self.summary = self.record = None
        self.saved_page = None
        self.refresh_saved_summaries()
        self.corners = []
        self.capture_time = captured_at or datetime.now(timezone.utc).isoformat()
        self.capture_id = capture_id
        self.input_origin = origin
        self.ocr_evidence_id = None
        self.ocr_raw_text = None
        self.review_history = []
        self.text.delete('1.0', 'end')
        self.text.edit_reset()
        self.text.edit_modified(False)
        if not self.summary_running:
            self.summary_run.set('就绪 · 等待生成摘要')
            self.summary_metrics.set('token/s：待统计')
            self.summary_activity.configure(mode='determinate', value=0)
            self.summary_activity.pack_forget()
        self.show_summary('待生成')
        self.draw(image)
        return True

    def choose(self):
        path = filedialog.askopenfilename(filetypes=[('图片', '*.png *.jpg *.jpeg *.bmp')])
        if path:
            image = cv2.imread(path)
            if image is None:
                self.status.set('图片读取失败')
            elif self.set_image(image, origin='imported-image'):
                self.show_workspace()
                self.status.set('已选择 '+path)

    def recognize_image(self, image, reading_order):
        if self.engine is None:
            self.engine = OCREngine()
        return self.engine.recognize(image, reading_order=reading_order)

    def ocr(self):
        if self.preview_in_use():
            return
        if self.image is None:
            self.status.set('请先选图或拍照')
            return
        image = self.image.copy()
        if self.busy:
            self.status.set('正在处理，请稍候')
            return
        if not self.confirm_replace_ocr():
            return
        close_review(self)
        self.summary = self.record = None
        self.refresh_saved_summaries()
        self.ocr_evidence_id = None
        self.show_summary('正在识别，请在OCR完成后重新生成摘要。')
        # 用当前图做 OCR，同时保留原图和校正步骤，便于验收时对照。
        original = self.raw_image.copy()
        operations = list(self.image_operations)
        reading_order = self.ocr_order()
        def operation():
            result, annotated = self.recognize_image(image, reading_order)
            identifier = None
            warning = ''
            if result['text']:
                try:
                    identifier = save_ocr_evidence(self.store.root, original, image, annotated, result, operations)
                except OSError as exc:
                    warning = ' · 验收图保存失败：'+str(exc)
            return result, annotated, identifier, warning
        def done(value):
            result, annotated, self.ocr_evidence_id, warning = value
            self.ocr_raw_text = result['text']
            self.review_history = []
            self.text.delete('1.0', 'end')
            self.text.insert('1.0', result['text'])
            self.text.edit_reset()
            self.draw(annotated)
            self.status.set('{}；OCR {:.3f} 秒'.format(result['status'], result['elapsed_seconds'])+warning)
        self.run(operation, done)

    def summarize(self):
        if self.preview_in_use():
            return
        start_summary(self)

    def review_ocr(self):
        generate_assistance(self, 'review')

    def format_ocr(self):
        generate_assistance(self, 'paragraphs')

    def ocr_order(self):
        return 'columns' if self.reading_order.get() == '先左栏后右栏' else 'rows'

    def add_knowledge(self):
        generate_assistance(self, 'knowledge')

    def save(self):
        if self.preview_in_use():
            return
        text = self.text.get('1.0', 'end-1c').strip()
        if self.image is None or not text:
            self.status.set('请先打开页面并识别或输入文字')
            return
        try:
            previous = self.saved_page
            if previous is not None:
                # 使用已有原图的字节，避免旧图重新编码后被当成新页面。
                image_bytes = self.store.image_path(self.saved_page).read_bytes()
            else:
                buffer = io.BytesIO()
                Image.fromarray(cv2.cvtColor(self.raw_image, cv2.COLOR_BGR2RGB)).save(buffer, format='PNG')
                image_bytes = buffer.getvalue()
            summary = (dict(self.summary) if self.summary and self.summary.get('source_text') == text
                       else self.store.ocr_source(text))
            ocr_only = summary.get('backend') == OCR_BACKEND
            if self.ocr_evidence_id:
                summary['ocr_evidence_id'] = self.ocr_evidence_id
            if self.ocr_raw_text is not None:
                summary['ocr_raw_text'] = self.ocr_raw_text
                if ocr_only and self.ocr_raw_text.strip() != text:
                    summary['manually_edited'] = True
            if self.review_history:
                summary['review_history'] = self.review_history
            if self.input_origin:
                summary['image_input_origin'] = self.input_origin
            if self.capture_id:
                summary['capture_id'] = self.capture_id
            page = self.store.save(image_bytes, text, summary,
                                   metadata={'captured_at': self.capture_time}, activate=not ocr_only)
            self.record = self.store.select_summary(page, self.store.summary_key(summary))
            self.saved_page = self.record
            self.summary = self.record['summary']
            self.refresh_saved_summaries()
            if ocr_only:
                self.show_summary(self.summary_display(self.summary))
            self.status.set('已保存 '+self.record['record_id'])
            if previous is not None:
                try:
                    self.draft_path(previous).unlink(missing_ok=True)
                except OSError as exc:
                    self.status.set('已保存记录，旧草稿清理失败：'+str(exc))
            return self.record
        except Exception as exc:
            self.status.set(str(exc))

    def save_from_view(self):
        if self.page_title.get() == '采集相册':
            self.feature_view.save_selected()
        else:
            self.save()

    def join_classroom(self):
        if self.page_title.get() == '采集相册':
            self.feature_view.join_classroom()
            return
        if self.page_title.get() != '页面工作台':
            self.status.set('请在页面工作台或采集相册选择要加入课堂的页面。')
            return
        if self.busy or self.preview_in_use():
            if self.busy:
                self.status.set('请等当前任务处理完成。')
            return
        text = self.text.get('1.0', 'end-1c').strip()
        if self.image is None or not text:
            self.status.set('请先打开页面并识别或输入文字。')
            return
        store = CollectionStore(self.store.root/'collections', self.store)

        def selected(name, identifier):
            record = self.save()
            if record is None:
                return
            reference = dict(record_id=record['record_id'], summary_key=record['active_summary_key'])
            try:
                group, added = store.merge_pages(name, [reference], identifier)
            except (OSError, KeyError, ValueError) as exc:
                self.status.set('加入课堂失败：'+str(exc))
                return
            self.collections(group['id'])
            self.status.set('已加入「{}」'.format(group['name']) if added else '当前页已在「{}」中'.format(group['name']))

        try:
            choose_classroom(self.root, store, text.splitlines()[0][:32], selected)
        except (OSError, KeyError, ValueError) as exc:
            self.status.set('无法打开课堂列表：'+str(exc))

    def history(self):
        return self.open_feature('历史记录', '打开页面后，在工作台查看、切换和编辑摘要。', show_history)

    def select_navigation(self, title, hint):
        self.page_title.set(title)
        self.page_hint.set(hint)
        self.save_button.text = '保存所选记录' if title == '采集相册' else '保存当前记录'
        self.save_button.paint()
        for name, button in self.navigation.items():
            button.show_selected(name == title)

    def show_workspace(self):
        if self.feature_view is not None:
            self.feature_view.destroy()
            self.feature_view = None
        self.workbench.pack(fill='both', expand=True)
        self.select_navigation('页面工作台', '导入页面，校对文字，再生成一份清晰笔记。')

    def open_feature(self, title, hint, make_view):
        if self.page_title.get() == title and self.feature_view is not None:
            return self.feature_view
        view = make_view(self, self.page_area)
        if self.feature_view is not None:
            self.feature_view.destroy()
        self.workbench.pack_forget()
        self.feature_view = view
        view.pack(fill='both', expand=True)
        self.select_navigation(title, hint)
        return view

    def refresh_saved_summaries(self):
        if self.saved_page is None:
            self.saved_sources_row.pack_forget()
            self.saved_sources.configure(values=())
            self.saved_sources.set('')
            return
        variants = self.saved_page['summaries']
        self.saved_sources.configure(values=[self.store.source_label(summary) for summary in variants.values()])
        if self.record:
            self.saved_sources.current(list(variants).index(self.record['active_summary_key']))
        else:
            self.saved_sources.set('选择已保存的摘要')
        if not self.saved_sources_row.winfo_manager():
            self.saved_sources_row.pack(fill='x', before=self.summary_status_label, pady=(6, 0))

    def switch_saved_summary(self, event=None):
        index = self.saved_sources.current()
        if self.saved_page is None or index < 0:
            return
        if self.busy:
            self.refresh_saved_summaries()
            self.status.set('正在处理，请稍后切换已保存的摘要')
            return
        variants = self.saved_page['summaries']
        key = list(variants)[index]
        if self.record and self.record['active_summary_key'] == key:
            return
        if self.record is None:
            text = self.text.get('1.0', 'end-1c').strip()
            has_draft = self.summary is not None or text != self.saved_page['text']
            if has_draft and not messagebox.askokcancel(
                '切换已保存的摘要', '切换前会保留本文的 OCR 校对草稿。继续切换吗？',
                parent=self.root, default=messagebox.CANCEL,
            ):
                self.refresh_saved_summaries()
                return
        if not self.preserve_page():
            self.refresh_saved_summaries()
            return
        if self.cap is not None:
            self.camera_stop()
        present_saved_summary(self, self.store.select_summary(self.saved_page, key))
        if self.summary is not None:
            self.status.set('已切换摘要及对应原文 · '+self.store.source_label(self.summary))

    def export(self):
        if self.preview_in_use():
            return
        if not self.record:
            self.status.set('请先保存或打开历史记录')
            return
        documents = Path.home()
        try:
            target = subprocess.check_output(['xdg-user-dir', 'DOCUMENTS'], text=True, timeout=1).strip()
            if target and Path(target).is_dir():
                documents = Path(target)
        except (OSError, subprocess.SubprocessError):
            pass
        path = filedialog.asksaveasfilename(parent=self.root, title='导出笔记到板端文件',
                                          filetypes=[('Markdown 笔记', '*.md'), ('纯文本', '*.txt')],
                                          initialdir=str(documents),
                                          initialfile='PPT-'+self.record['record_id'][:8])
        if not path:
            self.status.set('已取消导出笔记')
            return
        destination = Path(path)
        if not destination.suffix:
            destination = destination.with_suffix('.md')
        suffix = destination.suffix.lower()
        if suffix not in ('.md', '.txt'):
            self.status.set('请选择 Markdown（.md）或纯文本（.txt）')
            return
        try:
            writer = self.store.export_markdown if suffix == '.md' else self.store.export
            writer(self.record, destination)
        except (OSError, ValueError) as exc:
            self.status.set('笔记导出失败：'+str(exc))
            return
        self.status.set('已导出笔记 · '+str(destination.resolve()))

    def transmit(self):
        if self.preview_in_use():
            return
        if not self.record:
            self.status.set('请先保存或打开历史记录')
            return
        try:
            self.outbox.enqueue(self.host.get(), 5088, self.store.transport_record(self.record))
            self.status.set('已加入持久发送队列；可打开「发送队列」查看确认或失败原因')
        except (OSError, ValueError) as exc:
            self.status.set('未加入发送队列：'+str(exc))

    def pending_transmissions(self):
        return self.open_feature('发送队列', '检查电脑接收确认，或恢复待发送的记录。', show_outbox)

    def capture_album(self):
        return self.open_feature('采集相册', '管理采集照片，批量识别后生成多页综合摘要。', show_album)

    def collections(self, identifier=None):
        return self.open_feature('课堂整理', '选择多页并生成综合摘要，可从要点定位原页。',
                                 lambda app, parent: show_collections(app, parent, identifier))

    def toggle_auto_capture(self):
        if not self.auto_capture_enabled.get():
            self.finish_auto_capture()
        elif self.cap is None:
            self.camera_start()
        elif self.collector is None:
            self.capture_problem = ''
            self.last_collector = None
            try:
                self.collector = AutoCollector(self.store.root/'captures', device=self.cap.device)
            except (OSError, ValueError, KeyError) as exc:
                self.capture_problem = '采集相册无法打开：'+str(exc)
        self.capture_feedback.set(self.auto_capture_status())

    def finish_auto_capture(self):
        if self.collector is not None:
            self.collector.close()
            self.remember_camera_worker(self.collector.thread)
            self.last_collector, self.collector = self.collector, None

    def auto_capture_status(self):
        collector = self.collector or self.last_collector
        if collector is None:
            return self.capture_problem or '勾选即可开启相机，原图保存到「采集相册」'
        count = '本次已保存 {} 张'.format(collector.saved_count)
        reason = self.capture_problem or collector.note
        if self.collector is not None:
            return count+' · '+reason
        if collector.thread.is_alive():
            return '正在完成最后一帧…'
        if collector.saved_count:
            return count+' · '+(self.capture_problem or '可在「采集相册」查看')
        return '本次未采集 · '+reason

    def click_corner(self, event):
        if self.preview_in_use():
            return
        if self.image is not None and len(self.corners) < 4:
            offset_x, offset_y = self.image_offset
            x, y = (event.x-offset_x)/self.scale, (event.y-offset_y)/self.scale
            if 0 <= x < self.image.shape[1] and 0 <= y < self.image.shape[0]:
                self.corners.append((x, y))
                self.mark_corner(event.x, event.y)

    def mark_corner(self, x, y):
        self.canvas.create_oval(x-4, y-4, x+4, y+4, fill='red')

    def confirm_replace_ocr(self):
        text = editor_text(self).strip()
        baseline = (self.ocr_raw_text or '').strip()
        if self.saved_page is not None and text == self.saved_page['text']:
            return True
        return text == baseline or messagebox.askokcancel(
            '替换已编辑的 OCR', '此操作会替换当前校对文字。要继续吗？', parent=self.root,
            default=messagebox.CANCEL)

    def rectify(self):
        if self.preview_in_use():
            return
        if len(self.corners) != 4:
            self.status.set('请在图片上依次点击PPT的四个角，再点击四角校正')
            return
        if not self.confirm_replace_ocr():
            return
        try:
            operation = dict(kind='perspective', corners=list(self.corners), input_shape=list(self.image.shape))
            self.image = rectify_page(self.image, self.corners)
        except ValueError as exc:
            self.corners = []
            self.draw(self.image)
            self.status.set(str(exc))
            return
        self.image_operations.append(operation)
        close_review(self)
        self.corners = []
        self.version += 1
        self.summary = self.record = None
        self.refresh_saved_summaries()
        self.ocr_evidence_id = None
        self.ocr_raw_text = None
        self.review_history = []
        self.text.delete('1.0', 'end')
        self.show_summary('图片已校正，请重新OCR')
        self.draw(self.image)

    def ocr_region(self):
        if self.preview_in_use():
            return
        if self.busy:
            self.status.set('正在处理，请稍候')
            return
        if self.image is None or len(self.corners) != 2:
            self.status.set('请在预览选择区域的两个对角，再点击「区域补识」')
            return
        (ax, ay), (bx, by) = [(int(x), int(y)) for x, y in self.corners]
        x0, x1 = sorted((ax, bx))
        y0, y1 = sorted((ay, by))
        if x1-x0 < 16 or y1-y0 < 16:
            self.corners = []
            self.draw(self.page_preview)
            self.status.set('区域太小，请重新框选')
            return
        source = editor_text(self)
        anchor = self.text.index('insert lineend')
        region = self.image[y0:y1, x0:x1].copy()
        reading_order = self.ocr_order()
        self.corners = []
        self.draw(self.page_preview)
        close_review(self)

        def operation():
            result, _ = self.recognize_image(region, reading_order)
            return result['text']
        self.run(operation, lambda result: show_region(self, result, source, anchor, [x0, y0, x1, y1]))

    def compare_ocr(self):
        if self.preview_in_use():
            return
        if self.image is None:
            self.status.set('请先导入图片并进行区域校正')
            return
        if self.busy:
            self.status.set('正在处理，请稍候')
            return
        original, corrected = self.raw_image.copy(), self.image.copy()
        folder = self.store.root/'comparisons'
        try:
            unchanged = image_digest(original) == image_digest(corrected)
            existing = find_comparison(folder, original, None if unchanged else corrected)
            if existing:
                return show_comparison(self, existing)
            if unchanged:
                self.status.set('图片尚未改变，请先选择四角并执行区域校正')
                return
        except (OSError, ValueError) as exc:
            self.status.set('对照记录读取失败：'+str(exc))
            return
        operations = list(self.image_operations)
        def operation():
            if self.engine is None:
                self.engine = OCREngine()
            return compare(self.engine, original, corrected, folder, operations)
        def done(result):
            show_comparison(self, result)
            self.status.set('两份实际NPU识别结果已保存；对照不会替换人工校订原文或当前摘要')
        self.run(operation, done)

    def reset_image(self):
        if not self.preview_in_use() and self.raw_image is not None:
            self.version += 1
            self.image = self.raw_image.copy()
            self.image_operations = []
            self.corners = []
            self.draw(self.image)
            self.status.set('已还原图片，当前 OCR 和摘要仍可查看。')

    def camera_start(self):
        self.camera_stop()
        self.cap = Camera(self.device.get(), compensate=self.exposure_compensation.get())
        if self.auto_capture_enabled.get():
            self.toggle_auto_capture()
        self.status.set('正在打开摄像头…')

    def preview(self):
        if self.closed or self.cap is None:
            return
        if self.cap.error:
            error = self.cap.error
            if self.collector is not None:
                self.capture_problem = error
            self.camera_stop()
            self.status.set(error)
            return
        try:
            frame = self.cap.frames.get_nowait()
            self.frame = frame
            if self.collector is not None:
                self.collector.submit(frame)
            self.render_preview(frame)
            if not self.busy:
                mode = '曝光补偿已开启' if self.cap.compensate else '原始曝光'
                if '失败' in self.cap.note or '不可用' in self.cap.note or '不足' in self.cap.note:
                    mode = self.cap.note
                self.status.set('相机预览 · {} × {} · 拍照后识别 · {}'.format(frame.shape[1], frame.shape[0], mode))
        except queue.Empty:
            pass

    def preview_in_use(self):
        if self.cap is None:
            return False
        self.status.set('正在预览相机。拍照后处理新页面，或停止后继续处理当前页面。')
        return True

    def remember_camera_worker(self, thread):
        self.camera_workers_stopping = [worker for worker in self.camera_workers_stopping if worker.is_alive()]
        if thread.is_alive():
            self.camera_workers_stopping.append(thread)

    def camera_stop(self):
        self.finish_auto_capture()
        if self.cap is not None:
            self.cap.stop()
            self.remember_camera_worker(self.cap.thread)
            self.cap = None
            if not self.closed:
                self.display_image = self.page_preview
                redraw_preview(self)
        self.frame = None

    def stop_preview(self):
        if self.cap is None:
            return
        self.camera_stop()
        if self.auto_capture_enabled.get():
            self.capture_feedback.set(self.auto_capture_status())
            self.status.set('相机已停止 · 本次采集结果见预览下方')
        else:
            self.status.set('已停止预览，继续查看当前页面' if self.image is not None else '已停止预览')

    def capture(self):
        if self.frame is None:
            self.status.set('请先开启有效预览')
            return
        # 把当前预览帧复制为一张独立页面，供后续校正和识别。
        image = self.frame.copy()
        origin = 'camera:'+self.cap.device
        self.set_image(image, origin=origin)

    def request_close(self):
        if self.preserve_page():
            self.close()

    def close(self):
        self.closed = True
        self.outbox.close()
        if self.summarizer is not None:
            self.summarizer.cancel()
        self.camera_stop()
        camera_workers = list(self.camera_workers_stopping)
        def cleanup():
            try:
                if self.engine is not None:
                    self.engine.close()
            finally:
                # 给后台线程时间恢复相机曝光和释放设备，关窗本身不用等。
                deadline = time.monotonic()+6
                for worker in camera_workers:
                    worker.join(max(0, deadline-time.monotonic()))
        self.pool.submit(cleanup)
        self.pool.shutdown(wait=False)
        self.root.destroy()


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == '__main__':
    main()
