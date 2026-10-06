import copy
import hashlib
import hmac
import json
import sqlite3
import struct
from argparse import Namespace
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from wechat_report.__main__ import window
from wechat_report.crypto import Codec, HEADER, PAGE, checksum, decrypt_database, wal_frames
from wechat_report.demo import END, GROUP, START, demo_report, fixture
from wechat_report.messages import extract, parse_content, timestamp_unit
from wechat_report.report import export_files, render, statistics, validate
from wechat_report.storage import decrypted_snapshot, groups, stable_copy


@pytest.fixture
def database(tmp_path):
    root = tmp_path / 'source'
    fixture(root)
    return root


def test_selection_window_dedup_sender(database):
    assert groups(database, '虚构测试群') == []
    assert groups(database, GROUP['nick_name']) == [GROUP]
    messages, meta = extract(database, GROUP, START, END)
    assert len(messages) == 14
    assert messages[0]['timestamp'] == START.timestamp()
    assert all(START.timestamp() <= m['timestamp'] < END.timestamp() for m in messages)
    assert meta['rows_read'] == 15 and meta['duplicates_removed'] == 1
    assert statistics(messages)['speaker_count'] == 3
    assert messages[0]['sender'] == '小林 <产品>'
    assert any(len(m['provenance']) == 2 for m in messages)
    assert any(m.get('transcription_source') for m in messages)
    assert any(m['quote'] and m['quote']['svrid'] == '1005' for m in messages)
    assert all(not m['text'] for m in messages if m['type'] in ('图片', '视频', '表情'))


def test_same_name_groups(tmp_path):
    fixture(tmp_path / 'source', duplicate_name=True)
    assert len(groups(tmp_path / 'source', GROUP['nick_name'])) == 2


@pytest.mark.parametrize('unit', [1000, 1000000])
def test_real_queries_with_other_time_units(database, unit):
    table = 'Msg_' + hashlib.md5(GROUP['username'].encode()).hexdigest()
    for p in (database / 'message').glob('*.db'):
        db = sqlite3.connect(p)
        db.execute(f'UPDATE "{table}" SET create_time=create_time*?', (unit,))
        db.commit()
        db.close()
    messages, _ = extract(database, GROUP, START, END)
    assert len(messages) == 14 and messages[0]['timestamp'] == START.timestamp()


def test_conflicting_duplicate_is_not_silently_removed(database):
    table = 'Msg_' + hashlib.md5(GROUP['username'].encode()).hexdigest()
    db = sqlite3.connect(database / 'message/message_1.db')
    db.execute(f'UPDATE "{table}" SET message_content=?, WCDB_CT_message_content=0 WHERE server_id=1005',
               ('alice:\n同一个服务端ID却有不同内容',))
    db.commit()
    db.close()
    with pytest.raises(ValueError, match='内容冲突'):
        extract(database, GROUP, START, END)


def test_complete_fetch_and_batch_coverage(database, tmp_path):
    table = 'Msg_' + hashlib.md5(GROUP['username'].encode()).hexdigest()
    db = sqlite3.connect(database / 'message/message_0.db')
    db.executemany(f'INSERT INTO "{table}" VALUES(?,?,?,?,?,?,?)',
                   [(2000 + i, 2000 + i, 1, int(START.timestamp()) + 2000 + i, 7, 'alice:\n批次测试' + str(i), 0) for i in range(1205)])
    db.commit()
    db.close()
    messages, info = extract(database, GROUP, START, END)
    assert len(messages) == 1219 and info['rows_read'] == 1220
    directory = tmp_path / 'large'
    export_files(directory, {}, messages)
    manifest = json.loads((directory / 'batch-manifest.json').read_text())
    assert len(manifest) > 1 and sum(b['count'] for b in manifest) == 1219
    batches = [m for b in manifest for m in json.loads((directory / 'batches' / b['file']).read_text())]
    assert batches == messages


def test_timestamp_units_and_window():
    for unit in (1, 1000, 1000000):
        assert timestamp_unit(int(START.timestamp()) * unit) == unit
    with pytest.raises(ValueError):
        timestamp_unit(42)
    with pytest.raises(ValueError):
        window(Namespace(start='2026-09-20T00:00:00', end='2026-09-21T00:00:00', hours=24))
    for hours in (24, 48, 72, 168):
        a, b = window(Namespace(start=None, end=None, hours=hours))
        assert (b - a).total_seconds() == hours * 3600


def test_unknown_binary_and_entities():
    item = parse_content(b'\xff\x00\x01', 1, 4)
    assert item['warnings'] and item['text'] == ''
    item = parse_content('<!DOCTYPE msg [<!ENTITY x SYSTEM "file:///etc/passwd">]><msg>&x;</msg>', 49)
    assert item['warnings'] and not item['text']


def report_fixture(database, tmp_path):
    messages, _ = extract(database, GROUP, START, END)
    directory = tmp_path / 'output'
    meta = {'group_name': GROUP['nick_name'], 'group_id': GROUP['username'], 'start': START.isoformat(),
            'end': END.isoformat(), 'timezone': 'Asia/Shanghai', 'data_kind': '虚构测试', 'limitations': ['测试']}
    data = export_files(directory, meta, messages)
    manifest = json.loads((directory / 'batch-manifest.json').read_text())
    report = demo_report(data, manifest)
    return directory, data, manifest, report


def test_sources_coverage_and_escaping(database, tmp_path):
    directory, data, manifest, report = report_fixture(database, tmp_path)
    assert validate(data, report, manifest, directory)
    render(directory, data, report)
    page = (directory / 'index.html').read_text()
    assert '&lt;研发 &amp; 运营&gt;' in page and '<script>' not in page
    assert '<details>' in page and '<details open' not in page
    assert 'cdn.' not in page and 'src="http' not in page
    for mutation in ('id', 'quote', 'batch', 'stats', 'semantic'):
        r, d = copy.deepcopy(report), copy.deepcopy(data)
        if mutation == 'id':
            r['overview'][0]['evidence'][0]['message_id'] = 'made-up'
        elif mutation == 'quote':
            r['overview'][0]['evidence'][0]['quote'] = '凭空编造的事实'
        elif mutation == 'batch':
            r['reviewed_batches'] = []
        elif mutation == 'stats':
            d['metadata']['message_count'] += 1
        else:
            r['semantic_review']['completed'] = False
        with pytest.raises(ValueError):
            validate(d, r, manifest, directory)


def encrypt_page(codec, pgno, salt, fill=b'A'):
    plain = bytearray(fill * (PAGE - 80))
    if pgno == 1:
        plain[:16] = HEADER
        plain[16:18] = b'\x10\x00'
        plain[18:20] = b'\x02\x02'
        plain[20] = 80
    start = 16 if pgno == 1 else 0
    iv = bytes([pgno]) * 16
    enc = Cipher(algorithms.AES(codec.key), modes.CBC(iv)).encryptor()
    body = enc.update(bytes(plain[start:])) + enc.finalize()
    signed = body + iv
    mac = hmac.new(codec.mac, signed + struct.pack('<I', pgno), hashlib.sha512).digest()
    return (salt if pgno == 1 else b'') + signed + mac, bytes(plain) + bytes(80)


def make_wal(path, pages, endian='<', tail=b''):
    salt = b'wal-salt'
    header = struct.pack('>IIII', 0x377f0682 if endian == '<' else 0x377f0683, 3007000, PAGE, 0) + salt
    state = checksum(header, endian=endian)
    raw = header + struct.pack('>II', *state)
    for pgno, size, page in pages:
        first = struct.pack('>II', pgno, size)
        state = checksum(first + page, state, endian)
        raw += first + salt + struct.pack('>II', *state) + page
    path.write_bytes(raw + tail)


def test_authenticated_pages_committed_wal(tmp_path):
    secret, salt = b'S' * 32, b'T' * 16
    codec = Codec(secret, salt)
    first, plain1 = encrypt_page(codec, 1, salt)
    second, plain2 = encrypt_page(codec, 2, salt)
    changed, changed_plain = encrypt_page(codec, 2, salt, b'B')
    uncommitted, _ = encrypt_page(codec, 2, salt, b'C')
    source, target = tmp_path / 'input.db', tmp_path / 'plain.db'
    source.write_bytes(first + second)
    make_wal(Path(str(source) + '-wal'), [(2, 2, changed), (2, 0, uncommitted)])
    info = decrypt_database(source, target, secret)
    assert info['committed_frames'] == 1 and info['uncommitted_frames'] == 1
    assert target.read_bytes()[PAGE:] == changed_plain
    assert target.read_bytes()[18:20] == b'\x01\x01'
    damaged = bytearray(first)
    damaged[200] ^= 1
    with pytest.raises(ValueError, match='HMAC'):
        codec.decrypt(bytes(damaged), 1)
    with pytest.raises(ValueError, match='HMAC'):
        Codec(b'W' * 32, salt).decrypt(first, 1)


@pytest.mark.parametrize('endian', ['<', '>'])
def test_wal_checksums_and_truncated_tail(tmp_path, endian):
    path = tmp_path / 'wal'
    make_wal(path, [(1, 1, b'X' * PAGE)], endian, tail=b'unfinished')
    frames, info = wal_frames(path)
    assert len(frames) == 1 and info['ignored_tail_bytes'] == 10
    raw = bytearray(path.read_bytes())
    raw[90] ^= 1
    path.write_bytes(raw)
    with pytest.raises(ValueError, match='checksum'):
        wal_frames(path)


def test_wal_against_real_sqlite(tmp_path):
    path = tmp_path / 'sqlite.db'
    db = sqlite3.connect(path)
    db.execute('PRAGMA page_size=4096')
    db.execute('PRAGMA journal_mode=WAL')
    db.execute('PRAGMA wal_autocheckpoint=0')
    db.execute('CREATE TABLE test(value TEXT)')
    db.execute('INSERT INTO test VALUES("已提交内容")')
    db.commit()
    frames, info = wal_frames(Path(str(path) + '-wal'))
    assert info['committed_frames'] > 0
    copied = tmp_path / 'replayed.db'
    copied.write_bytes(path.read_bytes())
    with copied.open('r+b') as out:
        for pgno, _, page in frames:
            out.seek((pgno - 1) * PAGE)
            out.write(page)
        out.truncate(frames[-1][1] * PAGE)
    check = sqlite3.connect(copied)
    assert check.execute('SELECT value FROM test').fetchone()[0] == '已提交内容'
    assert check.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    check.close()
    db.close()


def test_snapshot_original_unchanged_and_cleanup(database, tmp_path, monkeypatch):
    from wechat_report import storage
    before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in database.rglob('*.db')}
    stable_copy(database, tmp_path / 'snapshot')
    assert before == {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in database.rglob('*.db')}
    observed = []
    def fail(source, target, secret):
        observed.append(source.parents[2])
        target.write_bytes(b'temporary cleartext')
        raise ValueError('injected failure')
    monkeypatch.setattr(storage, 'decrypt_database', fail)
    with pytest.raises(ValueError, match='injected'):
        with decrypted_snapshot(database, b'S' * 32):
            pass
    assert observed and all(not p.exists() for p in observed)


def test_detect_changes_during_copy(database, tmp_path, monkeypatch):
    from wechat_report import storage
    original = storage.shutil.copyfile
    def mutate(source, target):
        result = original(source, target)
        with source.open('ab') as f:
            f.write(b'changed fixture')
        return result
    monkeypatch.setattr(storage.shutil, 'copyfile', mutate)
    with pytest.raises(ValueError, match='变化'):
        stable_copy(database, tmp_path / 'snapshot')
