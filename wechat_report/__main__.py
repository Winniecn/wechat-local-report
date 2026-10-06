import argparse
import contextlib
import getpass
import json
import os
import platform
import plistlib
import selectors
import subprocess
import sys
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from .messages import extract
from .report import export_files, render, render_png, validate, write_json
from .storage import accounts, decrypted_snapshot, groups, group_candidates

TZ = ZoneInfo('Asia/Shanghai')


def capture_status(args, state, **details):
    if getattr(args, 'status_file', None):
        write_json(Path(args.status_file), {'state': state,
            'updated_at': datetime.now(TZ).isoformat(), **details})


def window(args):
    if bool(args.start) != bool(args.end):
        raise ValueError('--start 和 --end 必须同时提供')
    if args.start:
        start, end = datetime.fromisoformat(args.start), datetime.fromisoformat(args.end)
        if not start.tzinfo or not end.tzinfo:
            raise ValueError('起止时间必须含时区，例如 2026-09-20T20:00:00+08:00')
        start, end = start.astimezone(TZ), end.astimezone(TZ)
    else:
        end = datetime.now(TZ)
        start = end - timedelta(hours=args.hours)
    if start >= end:
        raise ValueError('起点必须早于终点')
    return start, end


def doctor():
    info = {'platform': platform.platform(), 'arch': platform.machine(), 'python': platform.python_version(),
            'accounts': [p.parent.name for p in accounts()], 'current_capture_verified': False,
            'validated_compatibility': {'macOS': '26.5.1', 'arch': 'arm64',
                'wechat': '4.1.13', 'build': '269602', 'date': '2026-09-20',
                'messages_exported': 437, 'note': '历史实测记录；不代表本次已获取密钥'}}
    app = Path('/Applications/WeChat.app/Contents/Info.plist')
    if app.exists():
        with app.open('rb') as f:
            plist = plistlib.load(f)
        info.update(wechat_version=plist.get('CFBundleShortVersionString'), wechat_build=plist.get('CFBundleVersion'))
    for p in accounts():
        with (p / 'contact/contact.db').open('rb') as f:
            encrypted = f.read(16) != b'SQLite format 3\0'
        info.setdefault('databases', []).append({'account': p.parent.name, 'encrypted': encrypted,
            'message_shards': len(list((p / 'message').glob('message_[0-9]*.db'))),
            'wal_exists': (p / 'contact/contact.db-wal').exists()})
    r = subprocess.run(['codesign', '-dvv', '/Applications/WeChat.app'], capture_output=True, text=True)
    info['hardened_runtime'] = 'runtime)' in r.stderr
    info['status'] = '环境探测不代表成功读取。真实读取需要本次账号有效 passphrase；不会自动修改微信签名或保存密钥。'
    print(json.dumps(info, ensure_ascii=False, indent=2))


def export(args):
    group_name = args.group or input('请输入完整群名：').strip()
    if not group_name:
        raise ValueError('群名不可为空')
    start, end = window(args)  # Freeze cutoff before snapshot / password prompt.
    available = accounts()
    if args.account:
        available = [p for p in available if p.parent.name == args.account]
    if len(available) != 1:
        raise ValueError('必须选择唯一账号 --account；可用账号：' + ', '.join(p.parent.name for p in accounts()))
    root = available[0]
    print(f'固定范围：[{start.isoformat()}, {end.isoformat()}) Asia/Shanghai')
    if args.capture_key:
        from .key_capture import capture
        print(('检查当前微信内存中的标准库密钥。' if args.capture_mode == 'raw' else '等待本次已授权的微信重新登录。') +
              '密钥仅通过进程间内存管道传递，不输出、不保存。', flush=True)
        capture_status(args, 'starting_capture')
        secret = capture(timeout=args.capture_timeout, sudo_lldb=args.sudo_lldb,
                         on_status=lambda state: capture_status(args, state), debug_app=args.debug_app,
                         mode=args.capture_mode, database_root=root, launch_app=args.launch_debug_app)
        capture_status(args, 'validating_candidate')
    elif not sys.stdin.isatty():
        raise ValueError('密钥仅允许交互式隐藏输入；不得通过命令参数、环境变量、日志或配置传入')
    else:
        raw = getpass.getpass('本次账号的 32 字节 passphrase（64 位 hex，不回显、不保存；不要粘贴进 Codex 聊天）：')
        try:
            secret = bytearray.fromhex(raw)
        except ValueError:
            raise ValueError('passphrase 格式无效') from None
        del raw
    if not isinstance(secret, dict) and len(secret) != 32:
        raise ValueError('passphrase 必须为 32 字节')
    try:
        with decrypted_snapshot(root, secret) as (plain, snapshot):
            candidates = groups(plain, group_name)
            if args.group_id:
                candidates = [g for g in candidates if g['username'] == args.group_id]
            if len(candidates) != 1:
                if not candidates and args.group_keyword:
                    alternatives = group_candidates(plain, args.group_keyword)
                else:
                    alternatives = candidates
                if not args.interactive_selection or not alternatives:
                    raise ValueError('完整群名未匹配唯一群；以下仅为候选元信息，不会自动读取：' + json.dumps(alternatives, ensure_ascii=False))
                capture_status(args, 'group_selection_required', candidates=alternatives)
                print('GROUP_SELECTION_REQUIRED：' + json.dumps(alternatives, ensure_ascii=False), flush=True)
                print('等待选择候选群的完整 username（最多10分钟，当前账号/时间保持不变）：', flush=True)
                with selectors.DefaultSelector() as selector:
                    selector.register(sys.stdin, selectors.EVENT_READ)
                    if not selector.select(600):
                        raise ValueError('群选择超时；清理本次明文快照与内存密钥')
                    choice = sys.stdin.readline().strip()
                candidates = [g for g in alternatives if g['username'] == choice]
                if len(candidates) != 1:
                    raise ValueError('必须选择清单中的唯一群ID；未读取其他群消息')
                group_name = candidates[0]['nick_name']
            group = candidates[0]
            messages, details = extract(plain, group, start, end)
            limitations = ['仅包含本机已同步、当前快照可读取的记录；无法证明该群历史已完整同步。',
                           '群内观点未作外部事实核查；未解析媒体仅列类型。']
            if not messages:
                limitations.append('此时间窗口未找到消息；不能据此断言群内无人发言，可能未同步。')
            if any(s['earliest_local'] > start.timestamp() for s in details['shards']):
                limitations.append('部分分片的本地最早消息晚于统计起点；需留意同步不足或分片轮转，无法据此判定完整性。')
            if any(m['warnings'] for m in messages):
                limitations.append('部分消息存在解析警告；详见 messages.json，不对其未解析内容作总结。')
            meta = {'group_name': group_name, 'group_id': group['username'], 'account': root.parent.name,
                    'start': start.isoformat(), 'end': end.isoformat(), 'timezone': 'Asia/Shanghai',
                    'interval': '[start, end)', 'data_kind': '真实本地记录', 'limitations': limitations,
                    'snapshot': snapshot, 'extraction': details}
            directory = Path(args.output) / (end.strftime('%Y%m%dT%H%M%S') + '-' + uuid.uuid4().hex[:8])
            export_files(directory, meta, messages)
    finally:
        if isinstance(secret, dict):
            for value in secret.values():
                value[:] = bytes(len(value))
            secret.clear()
        else:
            secret[:] = bytes(len(secret))
    print(f'导出完成：{directory.resolve()}\n共 {len(messages)} 条。下一步由当前 Codex 会话逐批阅读 batches，填写 report.json，再运行 finalize；此命令不执行独立 AI 总结。')
    capture_status(args, 'exported', directory=str(directory.resolve()), message_count=len(messages))


def finalize(args):
    directory = Path(args.directory)
    data = json.loads((directory / 'messages.json').read_text())
    report = json.loads((directory / 'report.json').read_text())
    manifest = json.loads((directory / 'batch-manifest.json').read_text())
    validate(data, report, manifest, directory)
    render(directory, data, report)
    result = render_png(directory, args.browser)
    write_json(directory / 'validation.json', {'structural_checks': 'passed', 'semantic_review': report['semantic_review'],
                                              'data_kind': data['metadata']['data_kind'], 'render': result})
    print(json.dumps(result, ensure_ascii=False, indent=2))


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description='本人微信本地记录导出与 Codex 总结报告')
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('doctor', help='只读检查本机环境')
    exp = sub.add_parser('export', help='读取本地加密库；仅导出，不独立 AI 总结')
    exp.add_argument('--group')
    exp.add_argument('--group-id')
    exp.add_argument('--group-keyword', help='完整群名未匹配时，仅列出含此关键词的群名与ID供确认，不读取消息')
    exp.add_argument('--interactive-selection', action='store_true', help='列出候选后最多等待10分钟选择ID；密钥仅留在当前进程，临时明文库退出时清理')
    exp.add_argument('--account')
    exp.add_argument('--capture-key', action='store_true', help='已明确授权调试与重新登录后使用；需系统允许附加，不会自动重签名')
    exp.add_argument('--capture-timeout', type=int, default=180, choices=range(10, 601), metavar='10..600',
                     help='等待本人手机确认和密钥派生的秒数，默认 180')
    exp.add_argument('--capture-mode', choices=['pbkdf', 'raw'], default='pbkdf',
                     help='pbkdf 等待登录派生；raw 在已登录进程中匹配标准密钥文本并逐库 HMAC 校验')
    exp.add_argument('--sudo-lldb', action='store_true', help='仅调试器使用终端已有 sudo 认证；不重签、不关闭 SIP，不保证系统允许附加')
    exp.add_argument('--status-file', help='可选状态 JSON；只记录阶段及程序错误，不记录调试器原始输出或密钥')
    exp.add_argument('--debug-app', help='只接受经 prepare_debug_copy.py 创建和验证的临时沙盒副本')
    exp.add_argument('--launch-debug-app', action='store_true', help='所有微信已退出后，从首条指令启动调试副本以捕获自动登录的派生')
    exp.add_argument('--hours', type=int, choices=[24, 48, 72, 168], default=24,
                     help='最近小时数；168 表示最近一周')
    exp.add_argument('--start')
    exp.add_argument('--end')
    exp.add_argument('--output', default='outputs')
    final = sub.add_parser('finalize', help='验证 Codex 总结来源并渲染离线报告')
    final.add_argument('directory')
    final.add_argument('--browser', help='可选本地 Chromium/Chrome 可执行文件路径')
    sub.add_parser('demo', help='虚构数据全链路测试，不代表真实读取成功')
    args = parser.parse_args()
    if getattr(args, 'sudo_lldb', False) and not args.capture_key:
        parser.error('--sudo-lldb 必须与 --capture-key 一起使用')
    if getattr(args, 'launch_debug_app', False) and (not args.debug_app or not args.capture_key or args.capture_mode != 'pbkdf'):
        parser.error('--launch-debug-app 需要 --debug-app、--capture-key 和 pbkdf 模式')
    try:
        if args.command == 'doctor':
            doctor()
        elif args.command == 'export':
            export(args)
        elif args.command == 'finalize':
            finalize(args)
        elif args.command == 'demo':
            from .demo import run
            run()
    except KeyboardInterrupt:
        capture_status(args, 'interrupted')
        print('已中断；临时快照已进入清理流程。', file=sys.stderr)
        return 130
    except (ValueError, OSError) as error:
        capture_status(args, 'failed', error=str(error))
        print(f'未完成：{error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
