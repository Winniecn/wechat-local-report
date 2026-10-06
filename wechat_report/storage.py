import contextlib
import hashlib
import os
import shutil
import signal
import sqlite3
import tempfile
import time
from pathlib import Path

from .crypto import decrypt_database

ROOT = Path.home() / 'Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files'


class SnapshotBusyError(ValueError):
    pass


def accounts():
    return sorted(p for p in ROOT.glob('*/db_storage') if (p / 'contact/contact.db').exists())


def sha256(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def required_files(root):
    dbs = [root / 'contact/contact.db'] + sorted((root / 'message').glob('message_[0-9]*.db'))
    dbs = [p for p in dbs if p.name == 'contact.db' or p.stem[8:].isdigit()]
    if len(dbs) < 2 or not dbs[0].is_file():
        raise ValueError('缺少联系人库或消息分片')
    return dbs


def fingerprint(paths):
    return {str(p): (p.stat().st_ino, p.stat().st_size, p.stat().st_mtime_ns, p.stat().st_ctime_ns)
            if p.exists() else None for p in paths}


def stable_copy(root, destination):
    dbs = required_files(root)
    paths = [p for db in dbs for p in (db, Path(str(db) + '-wal'))]
    if any(Path(str(p) + '-journal').exists() for p in dbs):
        raise ValueError('存在 rollback journal；当前快照方式不支持')
    before = fingerprint(paths)
    for p in paths:
        if before[str(p)] is not None:
            dest = destination / p.relative_to(root)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(p, dest)
            dest.chmod(0o600)
    if before != fingerprint(paths) or required_files(root) != dbs:
        raise SnapshotBusyError('复制期间微信数据库变化；请重试，持续繁忙时需退出微信后读取')
    hashes = {}
    for p in paths:
        if before[str(p)] is not None:
            digest = sha256(destination / p.relative_to(root))
            if sha256(p) != digest:
                raise SnapshotBusyError('快照二次校验失败；原文件变化')
            hashes[str(p.relative_to(root))] = digest
    if before != fingerprint(paths) or required_files(root) != dbs:
        raise SnapshotBusyError('校验期间数据库变化；拒绝混合快照')
    return dbs, hashes


def connect(path):
    db = sqlite3.connect(path.resolve().as_uri() + '?mode=ro&immutable=1', uri=True)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA query_only=ON')
    return db


@contextlib.contextmanager
def decrypted_snapshot(root, passphrase):
    old_handlers = {}
    def interrupted(signum, frame):
        raise KeyboardInterrupt('快照读取中断')
    for sig in (signal.SIGTERM, signal.SIGINT):
        old_handlers[sig] = signal.signal(sig, interrupted)
    try:
        with tempfile.TemporaryDirectory(prefix='wechat-report-') as temp:
            base = Path(temp)
            base.chmod(0o700)
            encrypted, plain = base / 'encrypted', base / 'plain'
            for attempt in range(30):
                try:
                    dbs, hashes = stable_copy(root, encrypted)
                    break
                except SnapshotBusyError:
                    if attempt == 29:
                        raise
                    if attempt == 0:
                        print('微信正在同步；保留本次内存密钥，等待稳定快照后重试（最多约1分钟）。', flush=True)
                    time.sleep(2)
            wal = {}
            for source in dbs:
                rel = source.relative_to(root)
                target = plain / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                wal[str(rel)] = decrypt_database(encrypted / rel, target, passphrase)
                target.chmod(0o600)
                with contextlib.closing(connect(target)) as db:
                    if [r[0] for r in db.execute('PRAGMA integrity_check')] != ['ok']:
                        raise ValueError('解密后 SQLite 完整性检查失败')
            yield plain, {'snapshot_method': 'stable-set-copy + double hash + integrity_check',
                          'snapshot_retries': attempt, 'encrypted_sha256': hashes, 'wal': wal}
    finally:
        for sig, handler in old_handlers.items():
            signal.signal(sig, handler)


def columns(db, table):
    return {r['name'] for r in db.execute('PRAGMA table_info("' + table.replace('"', '""') + '")')}


def require_columns(db, table, needed):
    missing = set(needed) - columns(db, table)
    if missing:
        raise ValueError(f'未验证的 schema：{table} 缺少 {sorted(missing)}')


def groups(root, name):
    with contextlib.closing(connect(root / 'contact/contact.db')) as db:
        require_columns(db, 'contact', ['username', 'nick_name'])
        return [dict(row) for row in db.execute(
            "SELECT username, nick_name FROM contact WHERE nick_name = ? AND username LIKE '%@chatroom'", (name,))]


def group_candidates(root, keyword):
    """Only group identity metadata, never messages or an automatic selection."""
    with contextlib.closing(connect(root / 'contact/contact.db')) as db:
        require_columns(db, 'contact', ['username', 'nick_name'])
        return [dict(row) for row in db.execute(
            "SELECT username, nick_name FROM contact WHERE instr(lower(nick_name),lower(?)) > 0 AND username LIKE '%@chatroom'",
            (keyword,))]
