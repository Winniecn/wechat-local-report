"""SQLCipher 4 candidate codec; authenticated fail-closed, no secret persistence.

Parameters checked against pinned wcdb-key-tool (MIT, see research/LICENSE).
WAL checksums follow sqlite.org/fileformat2.html, not the upstream decryptor.
"""
import hashlib
import hmac
import struct
from pathlib import Path

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

PAGE = 4096
HEADER = b'SQLite format 3\0'


def checksum(data, seed=(0, 0), endian='<'):
    if len(data) % 8:
        raise ValueError('WAL checksum 长度无效')
    a, b = seed
    words = struct.unpack(endian + 'I' * (len(data) // 4), data)
    for i in range(0, len(words), 2):
        a = (a + words[i] + b) & 0xffffffff
        b = (b + words[i + 1] + a) & 0xffffffff
    return a, b


def wal_frames(path, page_size=PAGE):
    """Return authenticated-format committed frames (still encrypted page data)."""
    if not path.exists() or path.stat().st_size == 0:
        return [], {'committed_frames': 0, 'uncommitted_frames': 0, 'ignored_tail_bytes': 0}
    raw = path.read_bytes()
    if len(raw) < 32:
        raise ValueError('WAL header 不完整')
    magic, version, size = struct.unpack('>III', raw[:12])
    if magic not in (0x377f0682, 0x377f0683) or version != 3007000 or size != page_size:
        raise ValueError('不支持的 WAL 格式')
    endian = '<' if magic == 0x377f0682 else '>'
    state = checksum(raw[:24], endian=endian)
    if state != struct.unpack('>II', raw[24:32]):
        raise ValueError('WAL header checksum 错误')
    frames, commit, pos, reason = [], 0, 32, None
    while pos + 24 + page_size <= len(raw):
        head = raw[pos:pos + 24]
        data = raw[pos + 24:pos + 24 + page_size]
        pgno, dbsize = struct.unpack('>II', head[:8])
        if head[8:16] != raw[16:24]:
            reason = '旧 WAL generation 的尾部已排除'
            break
        next_state = checksum(head[:8] + data, state, endian)
        if next_state != struct.unpack('>II', head[16:24]):
            # Ambiguous damage, don't silently discard potentially committed data.
            raise ValueError('WAL frame checksum 错误；拒绝不完整报告')
        if not pgno:
            raise ValueError('WAL 页号无效')
        frames.append((pgno, dbsize, data))
        state = next_state
        pos += 24 + page_size
        if dbsize:
            commit = len(frames)
    tail = len(raw) - pos
    return frames[:commit], {'committed_frames': commit, 'uncommitted_frames': len(frames) - commit,
                             'ignored_tail_bytes': tail, 'tail_reason': reason}


class Codec:
    def __init__(self, passphrase, salt):
        if isinstance(passphrase, dict):
            if salt not in passphrase or len(passphrase[salt]) != 32:
                raise ValueError('缺少此数据库已校验的内存密钥')
            self.key = bytes(passphrase[salt])
        else:
            self.key = hashlib.pbkdf2_hmac('sha512', passphrase, salt, 256000, 32)
        self.mac = hashlib.pbkdf2_hmac('sha512', self.key, bytes(v ^ 0x3a for v in salt), 2, 32)

    def decrypt(self, page, pgno):
        if len(page) != PAGE:
            raise ValueError('数据库页不完整')
        start = 16 if pgno == 1 else 0
        digest = hmac.new(self.mac, page[start:4032] + struct.pack('<I', pgno), hashlib.sha512).digest()
        if not hmac.compare_digest(digest, page[4032:]):
            raise ValueError(f'数据库页 {pgno} HMAC 校验失败；密钥或加密参数不匹配')
        decryptor = Cipher(algorithms.AES(self.key), modes.CBC(page[4016:4032])).decryptor()
        plain = decryptor.update(page[start:4016]) + decryptor.finalize()
        return (HEADER if pgno == 1 else b'') + plain + bytes(80)


def decrypt_database(source, target, passphrase):
    with source.open('rb') as f:
        first = f.read(PAGE)
    if first.startswith(HEADER):
        raise ValueError('真实读取入口预期加密数据库；明文请使用显式 fixture/import 流程')
    if source.stat().st_size % PAGE:
        raise ValueError('加密数据库尺寸不是完整页')
    codec = Codec(passphrase, first[:16])
    frames, info = wal_frames(Path(str(source) + '-wal'))
    # Verify all committed frame pages before producing a usable database.
    overlay = {}
    for pgno, dbsize, data in frames:
        overlay[pgno] = codec.decrypt(data, pgno)
    total = frames[-1][1] if frames else source.stat().st_size // PAGE
    with source.open('rb') as inp, target.open('xb') as out:
        for pgno in range(1, total + 1):
            encrypted = inp.read(PAGE)
            plain = overlay.get(pgno)
            if plain is None:
                plain = codec.decrypt(encrypted, pgno)
            if pgno == 1:
                if plain[16:18] != b'\x10\x00' or plain[20] != 80:
                    raise ValueError('解密后 SQLite 页参数不符')
                # A standalone, already replayed snapshot must not look for WAL.
                plain = plain[:18] + b'\x01\x01' + plain[20:]
            out.write(plain)
    return info
