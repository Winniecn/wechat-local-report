"""Transient LLDB capture. No resigning, offsets, key files or key logs.

Call only after the user authorizes debugging and the required WeChat re-login.
The LLDB child writes a candidate exclusively into a pipe consumed here. Never
forward its raw stdout/stderr (which may contain sensitive debugger output).
"""
import json
import os
import re
import signal
import selectors
import time
import subprocess
from pathlib import Path


def capture(timeout=180, sudo_lldb=False, on_status=None, debug_app=None, mode='pbkdf', database_root=None, launch_app=False):
    if not 10 <= timeout <= 600:
        raise ValueError('捕获等待必须为 10—600 秒')
    result = subprocess.run(['pgrep', '-x', 'WeChat'], capture_output=True, text=True)
    pids = result.stdout.split()
    allowed_executable = '/Applications/WeChat.app/Contents/MacOS/WeChat'
    if debug_app:
        from .debug_copy import verify_copy
        allowed_executable = str(verify_copy(debug_app) / 'Contents/MacOS/WeChat')
    if launch_app:
        if pids or not debug_app or mode != 'pbkdf':
            raise ValueError('启动采集要求所有微信已退出，且显式指定已验证的沙盒副本及 pbkdf 模式')
    else:
        if len(pids) != 1 or not pids[0].isdigit():
            raise ValueError('需要唯一运行中的微信进程；不能擅自选进程')
        executable = subprocess.run(['/bin/ps', '-p', pids[0], '-o', 'comm='],
                                capture_output=True, text=True)
        if str(Path(executable.stdout.strip()).resolve()) != allowed_executable:
            raise ValueError('运行中的微信不是本次指定并验证的应用；停止以免读取其他副本或账号。')
    helper = str(Path(__file__).with_name('lldb_capture.py').resolve())
    invocation = (f'lldb_capture.run(lldb.debugger, 0, {timeout}, {json.dumps(allowed_executable)})' if launch_app
                  else f'lldb_capture.run(lldb.debugger, {int(pids[0])}, {timeout})')
    if mode == 'raw':
        if database_root is None:
            raise ValueError('原始库密钥校验必须指定本次唯一账号目录')
        invocation = f'lldb_capture.run_raw(lldb.debugger, {int(pids[0])}, {timeout}, {json.dumps(str(database_root))})'
    commands = ('settings set target.preload-symbols false\n'
                f'command script import {json.dumps(helper)}\n'
                f'script {invocation}\nquit\n')
    command = ['/usr/bin/lldb', '--no-lldbinit']
    if sudo_lldb:
        # Authentication happens in the user's Terminal before this process.
        # Never prompt through a pipe or collect an administrator password here.
        auth = subprocess.run(['/usr/bin/sudo', '-n', 'true'], capture_output=True)
        if auth.returncode:
            raise ValueError('需要在本机终端先执行 sudo -v；不要向本会话提供管理员密码。')
        command = ['/usr/bin/sudo', '-n', *command]
    # sudo timestamps are tied to the controlling terminal. setsid() here
    # discards the successful Terminal authentication performed just above.
    child = subprocess.Popen(command, stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             start_new_session=not sudo_lldb)
    def interrupted(signum, frame):
        raise KeyboardInterrupt('捕获中断')
    previous_sigterm = signal.signal(signal.SIGTERM, interrupted)
    try:
        child.stdin.write(commands.encode())
        child.stdin.flush()
        out = b''
        ready = False
        deadline = time.monotonic() + timeout + 30
        with selectors.DefaultSelector() as selector:
            selector.register(child.stdout, selectors.EVENT_READ)
            while time.monotonic() < deadline:
                if selector.select(1):
                    chunk = os.read(child.stdout.fileno(), 65536)
                    if not chunk:
                        break
                    out += chunk
                    if not ready and b'WX_STATUS:READY' in out:
                        print('CAPTURE_READY：可以在微信中登录；不要将密码或验证码发送到本会话。', flush=True)
                        ready = True
                        if on_status:
                            on_status('capture_ready')
                    if b'WX_STATUS:ATTACH_DENIED' in out:
                        raise ValueError('系统拒绝调试器附加；未读取密钥。')
                    if b'WX_STATUS:SYMBOL_MISSING' in out:
                        raise ValueError('系统派生函数符号未解析；不使用猜测偏移。')
                elif child.poll() is not None:
                    break
            else:
                raise subprocess.TimeoutExpired('lldb', timeout)
    except subprocess.TimeoutExpired:
        # Interrupt only the child we own. Its LLDB finally block detaches.
        child.send_signal(signal.SIGINT)
        try:
            child.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            child.terminate()
            child.communicate(timeout=5)
        raise ValueError('LLDB 捕获超时；请确认微信已恢复响应，不输出调试器原始内容') from None
    finally:
        try:
            if child.poll() is None:
                child.send_signal(signal.SIGINT)
                try:
                    child.communicate(timeout=5)
                except subprocess.TimeoutExpired:
                    child.terminate()
                    child.communicate(timeout=5)
        finally:
            signal.signal(signal.SIGTERM, previous_sigterm)
    if mode == 'raw':
        match = re.search(rb'WX_PRIVATE_RAW:(\{[^\r\n]+\})', out)
        if match:
            values = json.loads(match.group(1))
            return {bytes.fromhex(salt): bytearray.fromhex(key) for salt, key in values.items()}
        count = re.search(rb'WX_STATUS:RAW_INCOMPLETE:(\d+/\d+)', out)
        if count:
            raise ValueError('当前内存中的标准密钥字面量未覆盖所需数据库（已校验 ' + count.group(1).decode() + '）；不猜测内存偏移。')
    match = re.search(rb'WX_PRIVATE_PIPE:([0-9a-f]{64})', out)
    if not match:
        # Classify known bootstrap failures without disclosing raw debugger
        # output, which must always remain within the private pipe.
        if b'sudo:' in out and (b'password is required' in out or b'terminal is required' in out):
            raise ValueError('调试器未启动：sudo 认证上下文不可用；不是微信登录失败。请从完成认证的同一终端运行。')
        if b'module importing failed' in out or b'NameError:' in out:
            raise ValueError('调试器辅助模块未能加载；未开始附加微信。')
        if b'Secret capture requires private pipe' in out:
            raise ValueError('调试器输出不是私有管道；为避免密钥泄漏，已停止。')
        if ready:
            raise ValueError('已成功附加，但等待期间未捕获符合条件的密钥派生；可能尚未完成手机确认或本版本采用其他派生路径。')
        raise ValueError('未取得可用候选密钥。可能因签名/调试权限不足，或没有发生重新登录；未修改微信。')
    return bytearray.fromhex(match.group(1).decode())
