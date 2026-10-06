"""Loaded ONLY by LLDB; raw output goes to key_capture's private pipe."""
import os
import stat
import time


def run_raw(debugger, pid, timeout, root):
    """Read standard SQLCipher hex literals; never use guessed struct offsets."""
    import hashlib
    import hmac
    import json
    import re
    import struct
    from pathlib import Path
    import lldb
    if not stat.S_ISFIFO(os.fstat(1).st_mode):
        raise RuntimeError('Secret capture requires private pipe; never run interactively')
    root = Path(root)
    files = [root / 'contact/contact.db'] + [p for p in (root / 'message').glob('message_*.db')
                                             if p.stem[8:].isdigit()]
    pages = {}
    for path in files:
        with path.open('rb') as stream:
            page = stream.read(4096)
        if len(page) == 4096:
            pages[page[:16]] = page
    debugger.SetAsync(True)
    target = debugger.CreateTarget(None)
    listener = lldb.SBListener('wechat-report-key-literals')
    error = lldb.SBError()
    process = target.AttachToProcessWithID(listener, pid, error)
    if not error.Success():
        print('WX_STATUS:ATTACH_DENIED', flush=True)
        return
    found = {}
    seen = set()
    scanned = 0
    try:
        regions = process.GetMemoryRegions()
        deadline = time.monotonic() + min(timeout, 90)
        pattern = re.compile(rb"[xX]'([0-9a-fA-F]{96}|[0-9a-fA-F]{64})'")
        for i in range(regions.GetSize()):
            info = lldb.SBMemoryRegionInfo()
            if not regions.GetMemoryRegionAtIndex(i, info) or not info.IsReadable() or not info.IsWritable():
                continue
            carry = b''
            address, end = info.GetRegionBase(), info.GetRegionEnd()
            while address < end and time.monotonic() < deadline and scanned < 1024**3:
                amount = min(1024**2, end - address)
                data = process.ReadMemory(address, amount, error)
                scanned += amount
                address += amount
                if not error.Success():
                    carry = b''
                    continue
                data = carry + data
                carry = data[-100:]
                for match in pattern.finditer(data):
                    candidate = bytes.fromhex(match.group(1).decode())
                    if candidate in seen:
                        continue
                    seen.add(candidate)
                    salts = [candidate[32:]] if len(candidate) == 48 else list(pages)
                    key = candidate[:32]
                    for salt in salts:
                        if salt not in pages or salt in found:
                            continue
                        mac = hashlib.pbkdf2_hmac('sha512', key, bytes(x ^ 0x3a for x in salt), 2, 32)
                        digest = hmac.new(mac, pages[salt][16:4032] + struct.pack('<I', 1), hashlib.sha512).digest()
                        if hmac.compare_digest(digest, pages[salt][4032:]):
                            found[salt] = key
                    if len(found) == len(pages):
                        print('WX_PRIVATE_RAW:' + json.dumps({s.hex(): k.hex() for s, k in found.items()}), flush=True)
                        return
            if time.monotonic() >= deadline or scanned >= 1024**3:
                break
        print('WX_STATUS:RAW_INCOMPLETE:' + str(len(found)) + '/' + str(len(pages)), flush=True)
    finally:
        if process.IsValid() and process.GetState() not in (lldb.eStateDetached, lldb.eStateExited):
            if process.GetState() == lldb.eStateRunning:
                process.Stop()
            process.Detach()


def run(debugger, pid, timeout, launch_path=None):
    import lldb
    if not stat.S_ISFIFO(os.fstat(1).st_mode):
        raise RuntimeError('Secret capture requires private pipe; never run interactively')
    debugger.SetAsync(True)
    target = debugger.CreateTarget(launch_path)
    listener = lldb.SBListener('wechat-report-own-process')
    error = lldb.SBError()
    if launch_path:
        launch = lldb.SBLaunchInfo([])
        launch.SetListener(listener)
        launch.SetLaunchFlags(lldb.eLaunchFlagStopAtEntry)
        launch.AddOpenFileAction(1, '/dev/null', False, True)
        launch.AddOpenFileAction(2, '/dev/null', False, True)
        process = target.Launch(launch, error)
    else:
        process = target.AttachToProcessWithID(listener, pid, error)
    if not error.Success():
        print('WX_STATUS:ATTACH_DENIED', flush=True)
        return
    bp = None
    try:
        arch = target.GetTriple().split('-')[0]
        if arch not in ('arm64', 'arm64e', 'aarch64', 'x86_64'):
            return
        bp = target.BreakpointCreateByName('CCKeyDerivationPBKDF')
        if bp.GetNumLocations() == 0 and not launch_path:
            print('WX_STATUS:SYMBOL_MISSING', flush=True)
            return
        # Parameters, not hardcoded memory addresses. PBKDF2 parameters are
        # independently checked against the actual database HMAC afterwards.
        arm = arch != 'x86_64'
        condition = '$x2 == 32 && $x4 == 16 && $x6 == 256000' if arm else '$rdx == 32 && $r8 == 16'
        bp.SetCondition(condition)
        process.Continue()
        print('WX_STATUS:READY', flush=True)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            event = lldb.SBEvent()
            if not listener.WaitForEvent(1, event):
                continue
            state = lldb.SBProcess.GetStateFromEvent(event)
            if state in (lldb.eStateExited, lldb.eStateDetached, lldb.eStateCrashed):
                return
            if state != lldb.eStateStopped:
                continue
            for thread in process:
                if thread.GetStopReason() != lldb.eStopReasonBreakpoint:
                    continue
                frame = thread.GetFrameAtIndex(0)
                address = frame.FindRegister('x1' if arm else 'rsi').GetValueAsUnsigned()
                secret = process.ReadMemory(address, 32, error)
                if error.Success() and len(secret) == 32:
                    print('WX_PRIVATE_PIPE:' + secret.hex(), flush=True)
                    return
            process.Continue()
    finally:
        if bp:
            target.BreakpointDelete(bp.GetID())
        if process.IsValid() and process.GetState() not in (lldb.eStateDetached, lldb.eStateExited):
            if process.GetState() == lldb.eStateRunning:
                process.Stop()
            process.Detach()
