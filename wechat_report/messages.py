import contextlib
import hashlib
import io
import re
import xml.etree.ElementTree as ET
from datetime import datetime

import zstandard

from .storage import columns, connect, require_columns, required_files

TYPES = {1: '文本', 3: '图片', 34: '语音', 43: '视频', 47: '表情', 48: '位置',
         49: '卡片', 50: '通话', 10000: '系统', 10002: '撤回/系统'}


def decode_content(value, compression=0):
    if value is None:
        return '', None
    if isinstance(value, str):
        return value, None
    try:
        if value.startswith(b'\x28\xb5\x2f\xfd'):
            with zstandard.ZstdDecompressor().stream_reader(io.BytesIO(value)) as reader:
                raw = reader.read(16 * 1024 * 1024 + 1)
            if len(raw) > 16 * 1024 * 1024:
                raise ValueError('单条消息解压超过 16 MiB')
            return raw.decode('utf-8'), None
        if compression:
            return '', '未知 WCDB 压缩格式，未解读内容'
        return value.decode('utf-8'), None
    except (UnicodeDecodeError, zstandard.ZstdError):
        return '', '二进制消息解码失败，未解读内容'


def parse_content(value, local_type, compression=0):
    text, warning = decode_content(value, compression)
    match = re.match(r'^([^\s:<>]+):\n', text)
    sender = match.group(1) if match else None
    if match:
        text = text[match.end():]
    kind = TYPES.get(int(local_type) & 0xffffffff, f'未知类型 {local_type}')
    result = {'sender_from_content': sender, 'type': kind, 'text': text if kind == '文本' else '',
              'links': [], 'card': None, 'quote': None, 'warnings': [warning] if warning else []}
    if kind in ('系统', '撤回/系统') and not text.lstrip().startswith('<'):
        result['text'] = text
    if text.lstrip().startswith('<') and len(text) <= 2 * 1024 * 1024:
        try:
            if '<!DOCTYPE' in text.upper() or '<!ENTITY' in text.upper():
                raise ValueError('不接受 XML 实体声明')
            node = ET.fromstring(text)
            app = node if node.tag == 'appmsg' else node.find('.//appmsg')
            if app is not None:
                result['card'] = {key: app.findtext(key) or '' for key in
                                  ['title', 'des', 'url', 'type', 'appattach/fileext', 'appattach/totallen']}
                result['text'] = '\n'.join(filter(None, [result['card']['title'], result['card']['des']]))
                record = app.findtext('recorditem')
                if record:
                    if '<!DOCTYPE' in record.upper() or '<!ENTITY' in record.upper():
                        raise ValueError('不接受嵌套 XML 实体声明')
                    record_node = ET.fromstring(record)
                    parts = []
                    for item in record_node.findall('.//dataitem'):
                        if item.get('datatype') == '1' and item.findtext('datadesc'):
                            parts.append(item.findtext('datadesc'))
                        elif item.get('datatype') == '2':
                            parts.append('[笔记内图片，内容未解析]')
                    if parts:
                        result['text'] = '\n\n'.join(parts)
                        result['content_source'] = '本地 XML recorditem/dataitem；未下载附件'
                feed = app.find('finderFeed')
                if feed is not None and feed.findtext('desc'):
                    result['text'] = feed.findtext('desc')
                    result['content_source'] = '视频号卡片附带文字说明；未解析视频内容'
                ref = app.find('refermsg')
                if ref is not None:
                    result['quote'] = {key: ref.findtext(key) or '' for key in
                                       ['svrid', 'type', 'displayname', 'content', 'createtime']}
                    result['quote']['content'] = redact_transport_keys(result['quote']['content'])
                url = result['card']['url']
                if url.startswith(('https://', 'http://')):
                    result['links'].append(url)
            # Use only an explicit transcription field, never infer audio content.
            transcript = node.find('.//voicetrans')
            if kind == '语音' and transcript is not None and transcript.get('transtext'):
                result['text'] = transcript.get('transtext')
                result['transcription_source'] = '本地消息 XML voicetrans@transtext'
        except (ET.ParseError, ValueError):
            result['warnings'].append('XML 无法解析；保留类型，不推断内容')
    if kind == '文本':
        result['links'] += re.findall(r'https?://[^\s<>"\u3000]+', result['text'])
    result['raw_text'] = redact_transport_keys(text)
    return result


def redact_transport_keys(text):
    """Strip attachment transport secrets while retaining source text and structure."""
    names = r'(?:aeskey|cdnthumbaeskey|cdndatakey|cdnthumbkey|thumbfilekey|key)'
    text = re.sub(r'(<(' + names + r')\b[^>]*>)[^<]+(</\2>)',
                  r'\1[REDACTED]\3', text, flags=re.IGNORECASE)
    text = re.sub(r'(&lt;(' + names + r')\b[^&]*&gt;).*?(&lt;/\2&gt;)',
                  r'\1[REDACTED]\3', text, flags=re.IGNORECASE | re.DOTALL)
    return re.sub(r'(\b(?:aeskey|cdnthumbaeskey|cdndatakey|cdnthumbkey)=)([\"\x27])[^\"\x27]+\2',
                  r'\1\2[REDACTED]\2', text, flags=re.IGNORECASE)


def timestamp_unit(value):
    value = int(value)
    if 946684800 <= value < 4102444800:
        return 1
    if 946684800000 <= value < 4102444800000:
        return 1000
    if 946684800000000 <= value < 4102444800000000:
        return 1000000
    raise ValueError('未知时间戳单位或时间超出 2000—2100 年范围')


def extract(root, group, start, end):
    table = 'Msg_' + hashlib.md5(group['username'].encode()).hexdigest()
    messages, schemas, shards = [], {}, []
    required = ['local_id', 'server_id', 'local_type', 'create_time', 'real_sender_id', 'message_content']
    with contextlib.closing(connect(root / 'contact/contact.db')) as contact:
        require_columns(contact, 'contact', ['username', 'nick_name'])
        def nickname(wxid):
            row = contact.execute('SELECT * FROM contact WHERE username=?', (wxid,)).fetchone()
            if not row:
                return wxid
            data = dict(row)
            return data.get('remark') or data.get('nick_name') or wxid
        for path in required_files(root)[1:]:
            with contextlib.closing(connect(path)) as db:
                exists = db.execute('SELECT 1 FROM sqlite_master WHERE type="table" AND name=?', (table,)).fetchone()
                if not exists:
                    continue
                require_columns(db, table, required)
                cols = columns(db, table)
                schemas[path.name] = sorted(cols)
                bounds = db.execute(f'SELECT MIN(create_time), MAX(create_time), COUNT(*) FROM "{table}"').fetchone()
                if not bounds[2]:
                    continue
                unit = timestamp_unit(bounds[0])
                if timestamp_unit(bounds[1]) != unit:
                    raise ValueError('同一消息表混合时间戳单位；拒绝猜测')
                shards.append({'file': path.name, 'target_table': table, 'timestamp_unit': unit,
                               'earliest_local': bounds[0] / unit, 'latest_local': bounds[1] / unit})
                names = {}
                ncols = columns(db, 'Name2Id')
                if 'user_name' in ncols:
                    # IDs refer to the message shard's own Name2Id, never contact.id.
                    def resolve(sid):
                        if sid not in names:
                            r = db.execute('SELECT user_name FROM Name2Id WHERE rowid=?', (sid,)).fetchone()
                            names[sid] = r[0] if r else None
                        return names[sid]
                else:
                    def resolve(sid):
                        return None
                cursor = db.execute(f'SELECT * FROM "{table}" WHERE create_time >= ? AND create_time < ? ORDER BY create_time, local_id',
                                    (start.timestamp() * unit, end.timestamp() * unit))
                while rows := cursor.fetchmany(500):
                    for row in rows:
                        r = dict(row)
                        parsed = parse_content(r['message_content'], r['local_type'], r.get('WCDB_CT_message_content', 0))
                        mapped = resolve(r['real_sender_id'])
                        embedded = parsed.pop('sender_from_content')
                        if mapped and embedded and mapped != embedded:
                            raise ValueError('消息正文发送人与 Name2Id 不一致，需核实 schema')
                        sender = embedded or mapped
                        if not sender:
                            sender = f'unknown:{path.name}:{r["real_sender_id"]}'
                            parsed['warnings'].append('发送人未解析，使用分片内稳定 ID')
                        server = str(r['server_id']) if r['server_id'] else None
                        identity = f'{group["username"]}:server:{server}' if server else f'{group["username"]}:{path.name}:{r["local_id"]}'
                        messages.append({'id': identity, 'server_id': server, 'local_id': r['local_id'],
                                         'timestamp': r['create_time'] / unit,
                                         'time': datetime.fromtimestamp(r['create_time'] / unit, start.tzinfo).isoformat(),
                                         'sender_id': sender, 'sender': nickname(sender), 'local_type': r['local_type'],
                                         'provenance': [{'database': path.name, 'table': table, 'local_id': r['local_id']}], **parsed})
    unique = {}
    duplicates = 0
    for m in messages:
        if m['id'] in unique:
            old = unique[m['id']]
            if any(old[k] != m[k] for k in ('timestamp', 'sender_id', 'local_type', 'raw_text')):
                raise ValueError('相同服务端 ID 的消息内容冲突，需人工检查')
            old['provenance'].extend(m['provenance'])
            duplicates += 1
        else:
            unique[m['id']] = m
    return sorted(unique.values(), key=lambda m: (m['timestamp'], m['id'])), {
        'rows_read': len(messages), 'duplicates_removed': duplicates, 'shards': shards, 'schemas': schemas}
