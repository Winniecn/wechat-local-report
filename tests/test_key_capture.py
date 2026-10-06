"""Exercise private-pipe capture without sudo, WeChat, or real secrets."""
import subprocess
import sys

import pytest

from wechat_report import key_capture


def fake_debugger(monkeypatch, output):
    real_popen = subprocess.Popen
    launches = []

    def run(args, **kwargs):
        result = '123\n' if args[0] == 'pgrep' else (
            '/Applications/WeChat.app/Contents/MacOS/WeChat\n' if args[0] == '/bin/ps' else '')
        return subprocess.CompletedProcess(args, 0, result, '')

    def popen(args, **kwargs):
        launches.append((args, kwargs['start_new_session']))
        code = 'import sys; sys.stdin.readline(); sys.stdout.buffer.write(' + repr(output) + '); sys.stdout.flush()'
        return real_popen([sys.executable, '-c', code], **kwargs)

    monkeypatch.setattr(key_capture.subprocess, 'run', run)
    monkeypatch.setattr(key_capture.subprocess, 'Popen', popen)
    return launches


def test_sudo_keeps_terminal_and_secret_stays_private(monkeypatch, capsys):
    synthetic = b'31' * 32
    launches = fake_debugger(monkeypatch, b'WX_STATUS:READY\nWX_PRIVATE_PIPE:' + synthetic + b'\n')
    phases = []
    result = key_capture.capture(timeout=10, sudo_lldb=True, on_status=phases.append)
    assert launches == [(['/usr/bin/sudo', '-n', '/usr/bin/lldb', '--no-lldbinit'], False)]
    assert result == bytearray(b'1' * 32)
    output = capsys.readouterr().out
    assert synthetic.decode() not in output and 'CAPTURE_READY' in output
    assert phases == ['capture_ready']


def test_sudo_bootstrap_failure_is_specific(monkeypatch):
    fake_debugger(monkeypatch, b'sudo: a password is required\n')
    with pytest.raises(ValueError, match='sudo 认证上下文不可用'):
        key_capture.capture(timeout=10, sudo_lldb=True)


def test_attach_denied_is_distinct_from_login_timeout(monkeypatch):
    fake_debugger(monkeypatch, b'WX_STATUS:ATTACH_DENIED\n')
    with pytest.raises(ValueError, match='系统拒绝调试器附加'):
        key_capture.capture(timeout=10)
