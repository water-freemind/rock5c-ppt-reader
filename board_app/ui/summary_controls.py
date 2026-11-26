"""摘要来源选择与实际阶段/速度展示；后台线程仅写事件队列。"""
import os
import queue
import time
import tkinter as tk
from tkinter import ttk
from board_app.summary.npu_summary import DEFAULT_MODEL, NpuSummarizer, installed_models
from board_app.summary.deepseek_summary import DeepSeekSummarizer, MODELS
from board_app.ui.ui_layout import FONT, INK, MUTED, ActionButton, repeat_while_open
from board_app.summary.credentials import CredentialStore

def setup_summary_controls(app):
    local_models = installed_models(app.store.root.parent)
    app.summary_provider = tk.StringVar(app.root, value='本地 NPU')
    app.summary_model = tk.StringVar(app.root, value=DEFAULT_MODEL)
    app.credentials = CredentialStore()
    try:
        saved_key = app.credentials.load()
    except (OSError, ValueError):
        saved_key = ''
        app.status.set('密钥配置暂时无法读取，请在密钥设置中重新保存')
    app.deepseek_key = tk.StringVar(app.root, value=saved_key or os.environ.get('DEEPSEEK_API_KEY', ''))
    app.summary_run = tk.StringVar(app.root, value='就绪 · 本地NPU / Qwen3-4B')
    app.summary_metrics = tk.StringVar(app.root, value='token/s：待统计')
    app.summary_events = queue.Queue()
    app.summary_running = False
    app.summary_cancelled = False
    app.summary_started = 0
    app.summary_job_version = 0
    app.summary_job_label = ''
    app.summary_phase = ''
    text_frame = app.output.master
    for child in text_frame.master.winfo_children():
        if isinstance(child, tk.Label) and child.cget('text') == '页面标题与 3～5 条要点，方便回顾与整理。':
            child.pack_forget()
    style = ttk.Style(app.root)
    style.configure('Summary.TCombobox', fieldbackground='#f7f9fd', background='#edf1f8',
                    foreground=INK, bordercolor='#e3e8f1', arrowsize=12, padding=4)
    style.map('Summary.TCombobox', fieldbackground=[('readonly', '#f7f9fd')],
              foreground=[('readonly', INK)])
    panel = tk.Frame(text_frame.master, bg='white', padx=18)
    panel.pack(fill='x', before=text_frame)
    controls = tk.Frame(panel, bg='white')
    controls.pack(fill='x')
    tk.Label(controls, text='生成模型', font=(FONT, 9), bg='white', fg=MUTED).pack(side='left', padx=(0, 7))
    provider = ttk.Combobox(controls, textvariable=app.summary_provider,
                           values=('本地 NPU', 'DeepSeek API'), state='readonly', width=11,
                           font=(FONT, 10), style='Summary.TCombobox')
    provider.pack(side='left', padx=(0, 6))
    model = ttk.Combobox(controls, textvariable=app.summary_model,
                        values=local_models, state='readonly', width=13,
                        font=(FONT, 10), style='Summary.TCombobox')
    model.pack(side='left', fill='x', expand=True)
    app.api_key_button = ActionButton(controls, 'API 密钥', lambda: api_settings(app), width=72, height=29)
    app.saved_sources_row = tk.Frame(panel, bg='white')
    tk.Label(app.saved_sources_row, text='查看已保存', font=(FONT, 9), bg='white',
             fg=MUTED).pack(side='left', padx=(0, 7))
    app.saved_sources = ttk.Combobox(app.saved_sources_row, state='readonly',
                                    font=(FONT, 10), style='Summary.TCombobox')
    app.saved_sources.pack(side='left', fill='x', expand=True)
    app.saved_sources.bind('<<ComboboxSelected>>', app.switch_saved_summary)
    app.summary_status_label = tk.Label(panel, textvariable=app.summary_run,
                                        font=(FONT, 9), bg='white', fg=INK, anchor='w', wraplength=430)
    app.summary_status_label.pack(fill='x', pady=(3, 0))
    app.summary_activity = ttk.Progressbar(panel, mode='determinate', value=0,
                                          style='Activity.Horizontal.TProgressbar')
    app.summary_metrics_label = tk.Label(panel, textvariable=app.summary_metrics,
                                         font=(FONT, 9), bg='white', fg=MUTED, anchor='w')
    app.summary_metrics_label.pack(fill='x')
    def cancel_summary():
        if app.summary_running and not app.summary_cancelled:
            app.summary_cancelled = True
            app.summarizer.cancel()
            app.summary_run.set(app.summary_job_label+' · 正在取消')
    app.summary_cancel_button = ActionButton(panel, '取消生成', cancel_summary, width=84, height=27)
    app.summary_combos = (provider, model)
    app.summary_privacy_badge = None
    def find_badge(widget):
        for child in widget.winfo_children():
            if isinstance(child, tk.Label) and child.cget('text') == '●  生成：本地 NPU':
                app.summary_privacy_badge = child
            find_badge(child)
    find_badge(app.root)
    def selected(event=None):
        cloud = app.summary_provider.get() == 'DeepSeek API'
        if cloud:
            app.api_key_button.pack(side='right', padx=(6, 0))
        else:
            app.api_key_button.pack_forget()
        values = MODELS if cloud else local_models
        model.configure(values=values)
        if app.summary_model.get() not in values:
            app.summary_model.set(values[0])
        if app.summary_privacy_badge is not None:
            app.summary_privacy_badge.configure(text='●  生成：DeepSeek 云端' if cloud else '●  生成：本地 NPU')
        if app.summary is not None:
            return
        app.summary_run.set('准备使用 · '+app.summary_provider.get()+' / '+app.summary_model.get())
        app.summary_metrics.set('token/s：待统计')
        app.summary_activity.stop()
        app.summary_activity.configure(mode='determinate', value=0)
    provider.bind('<<ComboboxSelected>>', selected)
    model.bind('<<ComboboxSelected>>', selected)
    app.refresh_summary_selection = selected
    def poll():
        if app.closed:
            return
        try:
            while True:
                version, event = app.summary_events.get_nowait()
                if app.summary_running and not app.summary_cancelled and version == app.summary_job_version:
                    app.summary_phase = event['stage']
                    app.summary_run.set(app.summary_job_label+' · '+event['stage'])
        except queue.Empty:
            pass
        if app.summary_running:
            app.summary_metrics.set('已用 {:.1f} 秒 · token/s：待统计'.format(time.monotonic()-app.summary_started))
            if not app.busy:
                app.summary_running = False
                app.summary_activity.stop()
                app.summary_activity.configure(mode='determinate', value=0)
                app.summary_cancel_button.pack_forget()
                for combo in app.summary_combos:
                    combo.configure(state='readonly')
                app.summary_run.set('处理结束 · 若输入已修改，请重新生成摘要')
    repeat_while_open(app.root, 100, poll)


def api_settings(app):
    dialog = tk.Toplevel(app.root)
    dialog.title('DeepSeek 摘要设置')
    dialog.configure(bg='white')
    dialog.transient(app.root)
    body = tk.Frame(dialog, bg='white', padx=24, pady=20)
    body.pack(fill='both', expand=True)
    tk.Label(body, text='DeepSeek API Key', font=(FONT, 12, 'bold'), bg='white', fg=INK).pack(anchor='w')
    ttk.Entry(body, textvariable=app.deepseek_key, show='*', width=45).pack(fill='x', pady=12)
    tk.Label(body, text='保存后，下次启动会自动读取板端私有配置。\n密钥不写入源码、日志、历史或导出文件。\n选择DeepSeek生成时，原文会发送至其云端服务。',
             font=(FONT, 10), justify='left', bg='white', fg=MUTED).pack(anchor='w')
    notice = tk.StringVar(dialog, value='')
    tk.Label(body, textvariable=notice, bg='white', fg=MUTED, font=(FONT, 10)).pack(anchor='w', pady=(8, 0))
    def save_key():
        try:
            app.credentials.save(app.deepseek_key.get())
        except (OSError, ValueError):
            notice.set('保存失败，请检查密钥格式及配置目录权限')
            return
        app.status.set('密钥已保存到当前用户私有配置，下次启动自动读取')
        dialog.destroy()
    ActionButton(body, '保存密钥', save_key, primary=True).pack(anchor='e', pady=(14, 0))


def start_summary(app):
    if app.busy:
        app.status.set('正在处理，请稍候')
        return
    # 读取编辑框中的当前文字，让人工校对的内容也参与摘要。
    text = app.text.get('1.0', 'end-1c').strip()
    if not text:
        app.status.set('原文为空，不生成摘要')
        return
    version = app.version
    auto_save = app.saved_page is not None
    progress = lambda event: app.summary_events.put((version, event))
    cloud = app.summary_provider.get() == 'DeepSeek API'
    try:
        backend = (DeepSeekSummarizer(app.deepseek_key.get(), model=app.summary_model.get(), progress=progress)
                   if cloud else NpuSummarizer(progress=progress, model=app.summary_model.get()))
    except ValueError as exc:
        if app.summary is None:
            app.summary_activity.configure(mode='determinate', value=0)
            app.show_summary(str(exc))
        app.status.set(str(exc))
        return
    app.summarizer = backend
    app.summary_job_version = version
    app.summary_job_label = ('DeepSeek API / ' if cloud else '本地NPU / ')+app.summary_model.get()
    app.summary_run.set(app.summary_job_label+' · 准备处理')
    app.summary_metrics.set('token/s：待统计')
    if app.summary is None:
        app.show_summary('正在生成摘要，处理阶段与速度显示在上方。')
    app.summary_running = True
    app.summary_cancelled = False
    app.summary_started = time.monotonic()
    for combo in app.summary_combos:
        combo.configure(state='disabled')
    app.summary_activity.configure(mode='indeterminate')
    app.summary_activity.pack(fill='x', before=app.summary_metrics_label, pady=(4, 2))
    app.summary_cancel_button.pack(anchor='e', before=app.summary_metrics_label)
    app.summary_activity.start(12)
    def operation():
        try:
            return backend.summarize(text)
        except Exception as exc:
            return {'_summary_error': str(exc)}
    def done(result):
        app.summary_running = False
        app.summary_activity.stop()
        app.summary_cancel_button.pack_forget()
        app.summary_activity.configure(mode='determinate', value=0 if app.summary_cancelled or '_summary_error' in result else 100)
        for combo in app.summary_combos:
            combo.configure(state='readonly')
        if app.summary_cancelled:
            if app.summary is None:
                app.show_summary('已取消生成。')
            app.summary_run.set(app.summary_job_label+' · 已取消')
            app.summary_metrics.set('token/s：已取消')
            app.status.set('已取消本次生成。')
            return
        if '_summary_error' in result:
            if app.summary is None:
                app.show_summary('摘要生成失败：\n'+result['_summary_error']+'\n\n请处理后点击“生成摘要”重试。')
            app.summary_run.set(app.summary_job_label+' · 生成失败，可重试')
            app.summary_metrics.set('token/s：未生成有效结果')
            app.status.set('失败，可重试：'+result['_summary_error'])
            return
        app.summary, app.record = result, None
        app.show_summary(app.summary_display(result))
        speed = result.get('tokens_per_second')
        basis = '端到端' if cloud else '生成'
        app.summary_metrics.set((basis+'速度 {:.2f} token/s'.format(speed) if speed is not None else 'token/s：未取得统计')+
                                ' · 共 {:.1f} 秒'.format(result['elapsed_seconds']))
        app.summary_run.set(app.summary_job_label+' · 已完成')
        status = '摘要完成；首字（含加载/连接）{:.2f}秒，总计{:.2f}秒{}'.format(
            result['first_token_seconds'], result['elapsed_seconds'],
            '；有内容需复核' if result.get('quality_warnings') else '')
        # 已存入历史的页面生成新摘要后，自动把当前模型的结果存回同一页。
        if auto_save:
            if app.save():
                app.summary_run.set(app.summary_job_label+' · 已自动保存')
                status = '摘要已自动保存到当前页 · '+status
            else:
                status = '摘要已生成，自动保存失败：'+app.status.get()+'。可点击「保存当前记录」重试。'
        app.status.set(status)
    app.run(operation, done)
