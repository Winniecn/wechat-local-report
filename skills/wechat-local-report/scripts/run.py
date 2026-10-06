#!/usr/bin/env python3
"""Project adapter: dry-run time planning and transparent engine execution."""
import argparse
import json
import os
from pathlib import Path
import shlex
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

TZ = ZoneInfo('Asia/Shanghai')
DEFAULT_PROJECT = Path.home() / 'Documents' / 'WeChatgroup'
DEFAULT_COPY = '/private/tmp/wechat-report-account-probe/WeChat.app'


def interval(period, start=None, end=None, now=None):
    if bool(start) != bool(end):
        raise ValueError('--start 和 --end 必须同时提供')
    if start:
        a, b = datetime.fromisoformat(start), datetime.fromisoformat(end)
        if a.tzinfo is None or b.tzinfo is None:
            raise ValueError('起止时间必须带时区')
        a, b = a.astimezone(TZ), b.astimezone(TZ)
    else:
        b = (now or datetime.now(TZ)).astimezone(TZ).replace(microsecond=0)
        midnight = b.replace(hour=0, minute=0, second=0)
        if period == 'today':
            a = midnight
        elif period == 'yesterday':
            a, b = midnight - timedelta(days=1), midnight
        else:
            hours = {'24h':24, '48h':48, '72h':72, 'week':168}[period]
            a = b - timedelta(hours=hours)
    if a >= b:
        raise ValueError('起点必须早于终点；零点整的今天尚无非空统计区间')
    return a, b


def project_paths(value):
    root = Path(value).expanduser().resolve()
    interpreter = root / '.venv/bin/python'
    if not (root / 'wechat_report/__main__.py').is_file() or not interpreter.is_file():
        raise ValueError('未找到现有项目与虚拟环境；请用 --project 指定 WeChatgroup 项目，不会自动下载或安装')
    return root, interpreter


def main(argv=None):
    p = argparse.ArgumentParser(description='微信 Skill：只读执行计划或调用现有引擎；不自动完成模型总结')
    p.add_argument('--project', default=str(DEFAULT_PROJECT))
    sub = p.add_subparsers(dest='command', required=True)
    plan = sub.add_parser('plan', help='只输出冻结时间和命令，不操作微信或写文件')
    plan.add_argument('--group', required=True)
    plan.add_argument('--group-id')
    plan.add_argument('--account')
    plan.add_argument('--group-keyword')
    plan.add_argument('--period', choices=['24h','48h','72h','week','today','yesterday'], default='24h')
    plan.add_argument('--start')
    plan.add_argument('--end')
    plan.add_argument('--debug-app', default=DEFAULT_COPY)
    engine = sub.add_parser('engine', add_help=False, help='透明调用项目CLI，stdin/stdout保持，按项目权限执行')
    engine.add_argument('--help', '-h', dest='engine_help', action='store_true')
    engine.add_argument('arguments', nargs=argparse.REMAINDER)
    args = p.parse_args(argv)
    try:
        root, interpreter = project_paths(args.project)
        if args.command == 'engine':
            if args.engine_help:
                args.arguments = ['--help']
            if not args.arguments or args.arguments[0] not in ('doctor','export','finalize','demo','--help','-h'):
                raise ValueError('engine 后需提供 doctor、export、finalize、demo 或 --help')
            os.chdir(root)
            os.execv(str(interpreter), [str(interpreter), '-m', 'wechat_report', *args.arguments])
        if not args.group.strip():
            raise ValueError('完整群名不能为空')
        a,b = interval(args.period,args.start,args.end)
        command=[str(interpreter),'-u','-m','wechat_report','export','--group',args.group,
                 '--start',a.isoformat(),'--end',b.isoformat(),'--capture-key',
                 '--capture-timeout','600','--launch-debug-app','--debug-app',args.debug_app]
        for flag,key in [('--account','account'),('--group-id','group_id'),('--group-keyword','group_keyword')]:
            if value:=getattr(args,key):command.extend([flag,value])
        if args.group_keyword:command.append('--interactive-selection')
        result={'mode':'plan_only','project':str(root),'group':args.group,'account':args.account,
                'start':a.isoformat(),'end':b.isoformat(),'timezone':str(TZ),'interval':'[start,end)',
                'argv':command,'shell_command':'cd '+shlex.quote(str(root))+' && '+shlex.join(command),
                'prerequisites':['本人账号与调试副本授权明确','版本与副本校验通过','原版微信正常退出'],
                'model_dependency':'导出后由当前 Codex 会话读完全部消息并填写 report.json，再 finalize'}
        print(json.dumps(result,ensure_ascii=False,indent=2))
        return 0
    except (ValueError,OSError) as e:
        print('未执行：'+str(e),file=sys.stderr)
        return 2


if __name__=='__main__':
    sys.exit(main())
