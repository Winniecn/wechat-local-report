"""Prepare a temporary, sandboxed copy of the already authorized WeChat app."""
import json
import plistlib
import subprocess
from pathlib import Path

from .storage import sha256

ORIGINAL = Path('/Applications/WeChat.app')


def entitlements(app):
    result = subprocess.run(['/usr/bin/codesign', '-d', '--entitlements', '-', '--xml', str(app)],
                            capture_output=True, check=True)
    return plistlib.loads(result.stdout)


def identity(app):
    return plistlib.loads((app / 'Contents/Info.plist').read_bytes())


def verify_copy(app):
    app = Path(app).resolve()
    if not str(app).startswith('/private/tmp/wechat-report-') or app == ORIGINAL:
        raise ValueError('仅支持本工具在 /private/tmp/wechat-report-* 创建的临时副本')
    manifest = json.loads((app.parent / 'debug-copy.json').read_text())
    info = identity(app)
    for key, value in manifest['identity'].items():
        if info.get(key) != value:
            raise ValueError('调试副本身份与清单不一致')
    if manifest['identity']['CFBundleIdentifier'] != 'com.tencent.xinWeChat':
        raise ValueError('正式读取必须使用原微信容器标识')
    if entitlements(app) != manifest['entitlements']:
        raise ValueError('调试副本权限与已审查清单不一致')
    if manifest['entitlements'].get('com.apple.security.app-sandbox') is not True:
        raise ValueError('禁止使用未保留 App Sandbox 的副本')
    for rel, digest in manifest['sha256'].items():
        if sha256(app / rel) != digest:
            raise ValueError('调试副本文件校验失败')
    subprocess.run(['/usr/bin/codesign', '--verify', '--deep', '--strict', str(app)],
                   check=True, capture_output=True)
    return app


def prepare(destination):
    destination = Path(destination).resolve()
    if not str(destination).startswith('/private/tmp/wechat-report-') or destination.exists():
        raise ValueError('目标必须是 /private/tmp/wechat-report-* 下尚不存在的副本')
    source_info = identity(ORIGINAL)
    if (source_info.get('CFBundleShortVersionString'), source_info.get('CFBundleVersion')) != ('4.1.13', '269602'):
        raise ValueError('目前只验证了本机 4.1.13 build 269602，版本变化需重新验证')
    subprocess.run(['/usr/bin/codesign', '--verify', '--deep', '--strict', str(ORIGINAL)], check=True)
    permissions = entitlements(ORIGINAL)
    if permissions.get('com.apple.security.app-sandbox') is not True:
        raise ValueError('原应用沙盒声明异常')
    permissions.pop('com.apple.developer.team-identifier', None)
    permissions['com.apple.security.get-task-allow'] = True
    permissions['com.apple.security.cs.disable-library-validation'] = True
    tracked = ['Contents/MacOS/WeChat', 'Contents/Info.plist', 'Contents/_CodeSignature/CodeResources',
               'Contents/Resources/wechat.dylib']
    source_hashes = {rel: sha256(ORIGINAL / rel) for rel in tracked}
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    subprocess.run(['/usr/bin/ditto', '--norsrc', '--noextattr', '--noacl', str(ORIGINAL), str(destination)], check=True)
    plist_path = destination.parent / 'sandbox-debug.plist'
    plist_path.write_bytes(plistlib.dumps(permissions))
    subprocess.run(['/usr/bin/codesign', '--force', '--sign', '-', '--options', '0',
                    '--entitlements', str(plist_path), str(destination)], check=True)
    if source_hashes != {rel: sha256(ORIGINAL / rel) for rel in tracked}:
        raise ValueError('原应用在准备期间发生变化，停止')
    manifest = {'identity': {k: source_info[k] for k in
                ('CFBundleIdentifier', 'CFBundleShortVersionString', 'CFBundleVersion')},
                'entitlements': permissions, 'original_sha256': source_hashes,
                'sha256': {rel: sha256(destination / rel) for rel in tracked},
                'original_modified': False, 'sandbox_retained': True}
    (destination.parent / 'debug-copy.json').write_text(json.dumps(manifest, indent=2) + '\n')
    verify_copy(destination)
    return destination
