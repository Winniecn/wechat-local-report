from wechat_report.messages import parse_content, redact_transport_keys


def test_note_full_body_instead_of_truncated_preview():
    source = '<msg><appmsg><type>24</type><des>预览</des><recorditem>&lt;recordinfo&gt;&lt;datalist&gt;&lt;dataitem datatype="1"&gt;&lt;datadesc&gt;完整正文及关键条件&lt;/datadesc&gt;&lt;/dataitem&gt;&lt;dataitem datatype="2"/&gt;&lt;/datalist&gt;&lt;/recordinfo&gt;</recorditem></appmsg></msg>'
    result = parse_content(source, 49)
    assert result['text'] == '完整正文及关键条件\n\n[笔记内图片，内容未解析]'
    assert not result['warnings']


def test_video_caption_is_not_video_transcription():
    result = parse_content('<msg><appmsg><title>需要升级</title><finderFeed><desc>作者的说明</desc></finderFeed></appmsg></msg>', 49)
    assert result['text'] == '作者的说明'
    assert '未解析视频' in result['content_source']
    assert 'transcription_source' not in result


def test_transport_keys_redacted_without_removing_evidence():
    result = parse_content('<msg><appmsg><title>保留正文</title><appattach><aeskey>fixture-secret</aeskey></appattach></appmsg></msg>',49)
    assert 'fixture-secret' not in result['raw_text']
    assert result['text'] == '保留正文'
    assert 'fixture-secret' not in redact_transport_keys('&lt;cdndatakey&gt;fixture-secret&lt;/cdndatakey&gt;')
