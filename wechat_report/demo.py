"""Deliberately fictional fixtures. Never used as a live-data fallback."""
import hashlib
import json
import sqlite3
import tempfile
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import zstandard

from .messages import extract
from .report import digest, export_files, render, render_png, validate, write_json
from .storage import groups

GROUP = {'username': 'fictional-project@chatroom', 'nick_name': '虚构测试群 <研发 & 运营>'}
START = datetime(2026, 9, 19, 20, 0, tzinfo=ZoneInfo('Asia/Shanghai'))
END = START + timedelta(hours=24)


def fixture(root, duplicate_name=False):
    (root / 'contact').mkdir(parents=True)
    (root / 'message').mkdir()
    db = sqlite3.connect(root / 'contact/contact.db')
    db.execute('CREATE TABLE contact(id INTEGER PRIMARY KEY,username TEXT,nick_name TEXT,remark TEXT)')
    db.executemany('INSERT INTO contact VALUES(?,?,?,?)', [(1, GROUP['username'], GROUP['nick_name'], ''),
        (20, 'alice', '小林 <产品>', ''), (30, 'bob', '陈工 & 开发', ''), (40, 'carol', '运营小周', '')])
    if duplicate_name:
        db.execute('INSERT INTO contact VALUES(5,?,?,?)', ('duplicate@chatroom', GROUP['nick_name'], ''))
    db.commit()
    db.close()
    texts = [
        (1, 1, 'alice', '建议把发布日改到周五，还需要大家确认。'),
        (2, 1, 'bob', '收到，我先评估；这不代表已经同意调整发布日期。'),
        (3, 1, 'carol', '客户反馈页出现重复订单，原因还没查清。'),
        (4, 1, 'bob', '重复订单问题已经修复，并完成回归测试。'),
        (5, 1, 'alice', '确认本次只上线修复，不调整发布日期。陈工请在9月21日18点前补齐回归记录。'),
        (6, 1, 'bob', '同意，我会补齐回归记录，目前还没提交。'),
        (7, 3, 'carol', '<msg><img aeskey="fictional"/></msg>'),
        (8, 34, 'carol', '<msg><voicemsg/><voicetrans transtext="下次讨论客服通知方案。"/></msg>'),
        (9, 49, 'alice', '<msg><appmsg><title>上线核对表</title><des>文档待补充</des><type>5</type><url>https://example.invalid/checklist</url><refermsg><svrid>1005</svrid><displayname>小林</displayname><content>确认本次只上线修复，不调整发布日期。</content></refermsg></appmsg></msg>'),
        (10, 1, 'carol', '客服通知还没决定由谁负责，也没有截止时间。'),
        (11, 47, 'alice', '<msg><emoji/></msg>'),
        (12, 43, 'bob', '<msg><videomsg/></msg>'),
        (13, 1, 'alice', '安全测试文本：<script>alert("XSS")</script> & 引号 "不应执行"。'),
        (14, 1, 'carol', '我觉得新页面更清晰，这是个人看法，还没做用户测试。'),
    ]
    table = 'Msg_' + hashlib.md5(GROUP['username'].encode()).hexdigest()
    records = []
    for n, kind, user, text in texts:
        body = (user + ':\n' + text).encode()
        compression = 4 if n in (5, 9) else 0
        if compression:
            body = zstandard.ZstdCompressor().compress(body)
        records.append((n, 1000 + n, kind, int(START.timestamp()) + (n - 1) * 100, {'alice': 7, 'bob': 8, 'carol': 9}[user], body, compression))
    for shard in range(2):
        db = sqlite3.connect(root / f'message/message_{shard}.db')
        db.execute('CREATE TABLE Name2Id(user_name TEXT)')
        for sid, name in [(7, 'alice'), (8, 'bob'), (9, 'carol')]:
            db.execute('INSERT INTO Name2Id(rowid,user_name) VALUES(?,?)', (sid, name))
        db.execute(f'CREATE TABLE "{table}"(local_id INTEGER,server_id INTEGER,local_type INTEGER,create_time INTEGER,real_sender_id INTEGER,message_content BLOB,WCDB_CT_message_content INTEGER)')
        rows = records[:8] if shard == 0 else [records[4], *records[8:]]
        db.executemany(f'INSERT INTO "{table}" VALUES(?,?,?,?,?,?,?)', rows)
        if shard == 0:
            db.executemany(f'INSERT INTO "{table}" VALUES(?,?,?,?,?,?,?)', [
                (98, 1098, 1, int(START.timestamp()) - 1, 7, 'alice:\n起点之前，不应读取', 0),
                (99, 1099, 1, int(END.timestamp()), 7, 'alice:\n恰好终点，不应读取', 0)])
        db.commit()
        db.close()


def demo_report(data, manifest):
    # The complete tiny fixture above has been read; these are explicit authored conclusions.
    by_server = {m['server_id']: m for m in data['messages']}
    def claim(text, status, ids, **kw):
        return {'text': text, 'status': status, 'evidence': [{'message_id': by_server[str(n)]['id'],
                    'quote': by_server[str(n)]['text']} for n in ids], **kw}
    return {'source_sha256': digest(data), 'reviewed_batches': manifest,
            'method': '虚构样例的人工编写总结，用于验证来源关系和渲染，不是独立 AI 总结器。',
            'semantic_review': {'completed': True, 'reviewer': 'Codex', 'notes': '逐条核对全部14条虚构消息，修复完成与记录提交未完成分开表述。'},
            'overview': [claim('讨论围绕重复订单修复、上线范围与后续记录展开。开发报告已修复并回归；本次确认只上线修复，不改变发布日期。回归记录和客服通知仍有后续工作。', '已确认', [1004, 1005, 1006, 1010])],
            'topics': [claim('改到周五发布只是早期建议；最终确认不调整发布日期。', '决定', [1001, 1005]),
                       claim('“收到”表示已看到消息，开发明确说当时尚未同意调整发布日期。', '收到', [1002]),
                       claim('运营认为新页面更清晰，但明确尚未做用户测试。', '群内观点', [1014])],
            'todos': [claim('补齐并提交回归记录。已同意负责，尚未提交。', '待处理', [1005, 1006], owner='陈工', due='9月21日18点前（原文；年份未明确）'),
                      claim('确定客服通知方案及负责人。', '未明确', [1008, 1010], owner='未明确', due='未明确')],
            'confirmed': [claim('开发在群内报告重复订单问题已修复并完成回归测试；本报告未独立检查代码或测试结果。', '执行完成', [1004]),
                          claim('本次上线范围仅包含修复。', '已确认', [1005])],
            'unresolved': [claim('客服通知负责人和时间要求尚未明确。', '未解决', [1010])],
            'other': [claim('群内分享上线核对表，卡片标注“文档待补充”；未打开外部链接验证文档。', '群内观点', [1009])]}


def run():
    with tempfile.TemporaryDirectory(prefix='wechat-demo-') as tmp:
        root = Path(tmp)
        fixture(root)
        candidates = groups(root, GROUP['nick_name'])
        assert candidates == [GROUP]
        messages, details = extract(root, GROUP, START, END)
    assert len(messages) == 14 and details['duplicates_removed'] == 1
    metadata = {'group_name': GROUP['nick_name'], 'group_id': GROUP['username'], 'account': 'fictional',
                'start': START.isoformat(), 'end': END.isoformat(), 'timezone': 'Asia/Shanghai', 'interval': '[start,end)',
                'data_kind': '虚构数据测试 · 非真实微信报告', 'extraction': details,
                'limitations': ['全部消息均为虚构测试数据，不代表真实微信读取成功。',
                                '图片、视频、表情仅有类型标记；语音文本来源为样例 XML 内已有转写。',
                                '报告仅针对所给消息；群内陈述未作外部核实。']}
    directory = Path('outputs') / ('demo-' + datetime.now().strftime('%Y%m%dT%H%M%S') + '-' + uuid.uuid4().hex[:6])
    data = export_files(directory, metadata, messages)
    manifest = json.loads((directory / 'batch-manifest.json').read_text())
    report = demo_report(data, manifest)
    validate(data, report, manifest, directory)
    write_json(directory / 'report.json', report)
    render(directory, data, report)
    render_info = render_png(directory)
    write_json(directory / 'validation.json', {'fixture': 'passed', 'real_wechat_read': 'not_verified',
                                             'count': len(messages), 'duplicates_removed': 1, 'render': render_info})
    print(str(directory.resolve()))
