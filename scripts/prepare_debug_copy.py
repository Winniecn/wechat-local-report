"""Create only the validated sandboxed temporary copy; never starts WeChat."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from wechat_report.debug_copy import prepare

if __name__ == '__main__':
    if len(sys.argv) != 2:
        raise SystemExit('Usage: prepare_debug_copy.py /private/tmp/wechat-report-NAME/WeChat.app')
    print(prepare(sys.argv[1]))
