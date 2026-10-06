"""Local browser QA: offline behavior, evidence toggles, mobile and split images."""
import json
import shutil
import struct
import sys
import tempfile
from pathlib import Path

from playwright.sync_api import sync_playwright
from wechat_report.report import render_png

directory = Path(sys.argv[1]).resolve()
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True, executable_path='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome')
    try:
        page = browser.new_page(viewport={'width': 960, 'height': 900})
        network = []
        page.on('request', lambda request: network.append(request.url) if request.url.startswith(('http:', 'https:')) else None)
        page.goto((directory / 'index.html').as_uri())
        metadata = json.loads((directory / 'messages.json').read_text())['metadata']
        assert page.locator('h1').inner_text().startswith(metadata['group_name'])
        assert page.locator('details[open]').count() == 0
        first = page.locator('details').first
        first.locator('summary').click()
        assert first.get_attribute('open') is not None
        assert first.locator('blockquote').first.is_visible()
        assert page.locator('script').count() == 0
        assert network == []
        page.screenshot(path=str(directory / 'desktop-check.png'))
        page.set_viewport_size({'width': 390, 'height': 844})
        assert page.evaluate('document.documentElement.scrollWidth') == 390
        page.screenshot(path=str(directory / 'mobile-check.png'), full_page=True)
    finally:
        browser.close()
with tempfile.TemporaryDirectory(prefix='wechat-render-qa-') as tmp:
    target = Path(tmp)
    shutil.copyfile(directory / 'index.html', target / 'index.html')
    shutil.copyfile(directory / 'summary.md', target / 'summary.md')
    manifest = render_png(target, max_height=1000)
    assert len(manifest['files']) > 1
    total_css_height = 0
    for name, (start, end) in zip(manifest['files'], manifest['segments']):
        raw = (target / name).read_bytes()
        assert raw[:8] == b'\x89PNG\r\n\x1a\n'
        width, height = struct.unpack('>II', raw[16:24])
        assert width == 1440
        assert abs(height - (end - start) * 1.5) <= 1
        total_css_height += end - start
    assert total_css_height == manifest['css_height']
print(json.dumps({'offline_requests': network, 'evidence_toggle': 'passed', 'mobile_width': 390,
                  'split_images': len(manifest['files']), 'split_full_coverage': True}, ensure_ascii=False))
