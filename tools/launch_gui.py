"""通过SSH在指定的现有VNC会话启动GUI，不操作终端窗口或认证。"""
from pathlib import Path
import os
import subprocess
import sys
from shared.paths import PROJECT_ROOT

root = PROJECT_ROOT
display = sys.argv[1] if len(sys.argv) > 1 else os.environ.get('DISPLAY')
if not display:
    raise SystemExit('请指定现有桌面，例如 env/bin/python -m tools.launch_gui :1')
environment = os.environ.copy()
environment['DISPLAY'] = display
environment.setdefault('XAUTHORITY', str(Path.home()/'.Xauthority'))
# 教学包Tk为noxft版本；本板现有系统Tcl/Tk同为8.6.13且支持Xft。
# 仅对子进程选用现有库，使中文字体抗锯齿；不覆盖系统或Conda文件。
system_tk = [Path('/usr/lib/aarch64-linux-gnu/libtcl8.6.so'), Path('/usr/lib/aarch64-linux-gnu/libtk8.6.so')]
if all(path.exists() for path in system_tk):
    environment['LD_PRELOAD'] = ':'.join(str(path) for path in system_tk)
(root/'logs').mkdir(exist_ok=True)
with (root/'logs/gui.log').open('ab') as log:
    child = subprocess.Popen([str(root/'env/bin/python'), '-u', '-m', 'board_app'],
                             cwd=root, env=environment, stdout=log, stderr=log, start_new_session=True)
print('GUI进程', child.pid, '桌面', display, '实际窗口仍待观察')
