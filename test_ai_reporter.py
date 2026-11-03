from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
from unittest.mock import Mock
import pytest
from ai_reporter.config import load
from ai_reporter.demo import snapshot
from ai_reporter.evidence import load_documents, validate_document
from ai_reporter.generation import fallback, validate, SCHEMA
from ai_reporter.input import build, select_records
from ai_reporter.render import report_object, html_report, telegram, review_status
from ai_reporter.state import State
from ai_report_runner import create, send, main


def cfg(tmp_path):
    c = load()
    c['state_file'] = str(tmp_path / 'state.sqlite3')
    c['output_directory'] = str(tmp_path / 'reports')
    c['documents_file'] = str(tmp_path / 'documents.json')
    c['backtest_directory'] = str(tmp_path / 'backtests')
    c['provider']['min_interval_seconds'] = 0
    return c


def doc(s, text='회사는 배당 정책을 유지한다고 발표했다.', **overrides):
    yesterday = datetime.now(timezone.utc) - timedelta(days=1)
    result = {'id': 'doc:one', 'title': '공식 보도자료', 'publisher': '가상 회사',
        'url': 'https://example.org/official', 'instrument_id': s['instrument_id'],
        'event_date': yesterday.date().isoformat(), 'published_at': yesterday.isoformat(),
        'retrieved_at': datetime.now(timezone.utc).isoformat(), 'language': 'ko',
        'version': '1', 'sha256': 'a' * 64, 'excerpt': text,
        'excerpt_sha256': hashlib.sha256(text.encode()).hexdigest(),
        'full_text_verified': True, 'source_type': 'ir'}
    result.update(overrides)
    return result


def response(text='회사는 배당 정책을 유지한다고 발표했다.', kind='confirmed_fact', source='doc:one'):
    return {'summary': ['추가 검토가 필요합니다.'], 'investment_thesis':
            [{'text': text, 'evidence_ids': [source], 'quote': text, 'kind': kind}],
            'counterarguments': [], 'related_events': [],
            'scenarios': {'optimistic': '실적 회복이 확인되는 경우.', 'base': '현상 유지가 확인되는 경우.',
                          'pessimistic': '배당이 줄어드는 경우.'},
            'monitoring_items': ['다음 공식 공시'], 'limitations': ['자료 범위가 제한됩니다.']}


def test_company_identity_and_future_document_rejected(tmp_path):
    s = snapshot('kr')
    a = doc(s)
    b = doc(s, id='future', published_at=(datetime.now(timezone.utc) + timedelta(days=2)).isoformat())
    c = doc(s, id='other', instrument_id='us:OTHER:CODE:ADR')
    path = tmp_path / 'docs.json'
    path.write_text(json.dumps([a, b, c], ensure_ascii=False), encoding='utf-8')
    found, rejected = load_documents(path, s['instrument_id'], s['as_of'])
    assert [d['id'] for d in found] == ['doc:one']
    assert {r['reason'] for r in rejected} == {'FUTURE_DOCUMENT', 'OTHER_COMPANY_DOCUMENT'}


def test_dedup_republication_and_verified_excerpt(tmp_path):
    s = snapshot()
    same = doc(s, id='copy', publisher='다른 매체', original_document_id='first')
    first = doc(s, id='first', original_document_id='first')
    old = doc(s, id='old', source_type='news', event_date=(datetime.now(timezone.utc) - timedelta(days=40)).date().isoformat())
    bad = doc(s, id='hash', excerpt_sha256='0' * 64)
    path = tmp_path / 'docs.json'
    path.write_text(json.dumps([first, same, old, bad], ensure_ascii=False), encoding='utf-8')
    selected, rejected = load_documents(path, s['instrument_id'], s['as_of'])
    assert len(selected) == 1
    assert {r['reason'] for r in rejected} == {'DUPLICATE_OR_SYNDICATED_DOCUMENT',
                                              'NEWS_OUTSIDE_LOOKBACK_OR_REPUBLICATION', 'UNVERIFIED_EXCERPT_OR_HASH'}


def test_unknown_evidence_numeric_and_unverified_claim_blocked():
    s = snapshot()
    s['documents'] = [doc(s)]
    assert validate(response(), s)[1] == []
    assert validate(response(source='no-such-doc'), s)[1]
    assert validate(response('목표주가 900원'), s)[1]
    assert validate(response('배당 정책 유지', kind='ai_inference'), s)[1]
    result, issues = validate(response('회사 파산', kind='unverified', source='doc:one'), s)
    assert issues and not result['investment_thesis']


def test_fact_requires_exact_quote_not_just_existing_source():
    s = snapshot()
    s['documents'] = [doc(s)]
    bad = response('회사는 파산했다.')
    bad['investment_thesis'][0]['quote'] = '회사는 배당 정책을 유지한다고 발표했다.'
    checked, issues = validate(bad, s)
    assert issues == ['QUOTE_NOT_VERIFIED:investment_thesis'] and checked['investment_thesis'] == []
    correct = response()
    assert validate(correct, s)[0]['investment_thesis'][0]['text'] == correct['investment_thesis'][0]['text']


@pytest.mark.parametrize('market', ['kr', 'us', 'eu'])
def test_demo_identity_each_market(market):
    s = snapshot(market)
    assert s['instrument_id'].startswith(market + ':') and s['name'] == '가상기업'


def test_high_risk_and_missing_score_cannot_be_promoted():
    s = snapshot()
    s['risk_status'] = 'HIGH_RISK'
    assert review_status(s) == '위험 요인 우선 확인'
    s['risk_status'] = 'INSUFFICIENT_DATA'
    assert review_status(s) == '데이터 부족'
    s['risk_status'] = 'NORMAL'
    s['metrics']['investment_score']['value'] = None
    s['evaluation_status'] = 'UNRATED'
    assert review_status(s) == '데이터 부족'


def test_offline_fallback_cache_and_document_change(tmp_path):
    c = cfg(tmp_path)
    s = snapshot()
    state = State(c['state_file'])
    a, reused = create(s, c, state, offline=True)
    b, reused_second = create(s, c, state, offline=True)
    assert not reused and reused_second and a['report_id'] == b['report_id']
    assert a['created_at'] == b['created_at'] and a['model_usage']['input_tokens'] is None
    s['documents'] = [doc(s)]
    changed, reused_third = create(s, c, state, offline=True)
    assert not reused_third and changed['report_id'] != a['report_id']
    assert state.db.execute('SELECT COUNT(*) FROM ai_calls').fetchone()[0] == 0
    state.close()


def test_mock_ai_response_usage_cost_budget_and_validation_retry(tmp_path, monkeypatch):
    c = cfg(tmp_path)
    c['provider']['enabled'] = True
    s = snapshot()
    s['documents'] = [doc(s)]
    monkeypatch.setenv('OPENAI_API_KEY', 'test-only')
    provider = Mock()
    provider.generate.side_effect = [(response('목표주가 900원'), {'input_tokens': 200, 'output_tokens': 100}),
                                     (response(), {'input_tokens': 210, 'output_tokens': 110})]
    state = State(c['state_file'])
    report, _ = create(s, c, state, provider)
    assert report['mode'] == 'AI_VALIDATED' and provider.generate.call_count == 2
    assert report['model_usage']['input_tokens'] == 410
    assert report['model_usage']['output_tokens'] == 210
    assert report['model_usage']['estimated_usd'] > 0
    assert state.db.execute('SELECT COUNT(*) FROM ai_calls').fetchone()[0] == 2
    state.close()


def test_timeout_and_daily_limit_fallback(tmp_path, monkeypatch):
    c = cfg(tmp_path)
    c['provider']['enabled'] = True
    c['provider']['max_calls_per_instrument_day'] = 1
    s = snapshot()
    monkeypatch.setenv('OPENAI_API_KEY', 'test-only')
    provider = Mock()
    provider.generate.side_effect = TimeoutError()
    state = State(c['state_file'])
    r, _ = create(s, c, state, provider)
    assert r['mode'] == 'QUANTITATIVE_FALLBACK'
    assert state.db.execute('SELECT COUNT(*) FROM ai_calls').fetchone()[0] == 1
    s['issues'].append('updated')
    again, _ = create(s, c, state, provider)
    assert again['mode'] == 'QUANTITATIVE_FALLBACK' and provider.generate.call_count == 1
    state.close()


def test_html_escape_and_telegram_unicode_limit():
    s = snapshot()
    s['name'] = '<script>alert(1)</script>'
    c = load()
    report = report_object(s, fallback(s), 'demo', datetime.now(timezone.utc).isoformat(),
                           'FALLBACK', {}, {'valid': True})
    html = html_report(report)
    assert '<script>' not in html and '&lt;script&gt;' in html
    report['investment_thesis'][0]['text'] = '😀' * 5000
    msg = telegram(report, 3400)
    assert len(msg.encode('utf-16-le')) // 2 <= 3400


def test_preview_never_sends_and_uncertain_delivery_not_retried(tmp_path, monkeypatch):
    c = cfg(tmp_path)
    s = snapshot()
    state = State(c['state_file'])
    report, _ = create(s, c, state, offline=True)
    sender = Mock(side_effect=RuntimeError('network uncertain'))
    import daily_runner
    monkeypatch.setattr(daily_runner, 'send_message', sender)
    monkeypatch.setenv('TELEGRAM_BOT_TOKEN', 'test-token')
    monkeypatch.setenv('TELEGRAM_CHAT_ID', 'test-chat')
    assert sender.call_count == 0
    with pytest.raises(RuntimeError):
        send(report['report_id'], c, state)
    assert state.delivery(report['report_id']) == 'UNCERTAIN'
    with pytest.raises(ValueError):
        send(report['report_id'], c, state)
    assert sender.call_count == 1
    sender.side_effect = None
    assert send(report['report_id'], c, state, retry_uncertain=True) == 'SENT'
    assert send(report['report_id'], c, state) == 'ALREADY_SENT'
    assert sender.call_count == 2
    state.close()


def test_real_audit_shape_selected_and_incomplete_status(tmp_path):
    c = cfg(tmp_path)
    source = tmp_path / 'existing'
    source.mkdir()
    value = {'market': 'us', 'code': 'SYN', 'name': '가상사', 'as_of': datetime.now(timezone.utc).date().isoformat(),
             'candidate': True, 'current': 30, 'currency': 'USD', 'drawdown': -40,
             'shareholder_grade': 'A', 'industry': 'GENERAL', 'yield_pct': 4, 'avg3_pct': 2,
             'fundamental_status': 'INSUFFICIENT_DATA', 'fundamental_missing': ['eps'],
             'valuation': {'current': {'price': {'evidence': [{'source': 'SYNTHETIC'}]}}, 'metrics': {}, 'band': 'N/A'},
             'score': {'adjusted_score': None, 'coverage_pct': 40, 'evaluation_status': 'UNRATED', 'grade': None},
             'events': [], 'issues': [], 'failed': False}
    (source / 'investment.audit.json').write_text(json.dumps([value]), encoding='utf-8')
    assert select_records(source, c, 'us:SYN')[0]['code'] == 'SYN'
    built = build(value, source, c)
    assert built['instrument_id'] == 'us:UNKNOWN:SYN:ORDINARY_UNVERIFIED'
    assert built['backtest']['status'] == '평가 불가'
    assert review_status(built) == '데이터 부족'


def test_cli_demo_does_not_use_provider_and_writes_three_reports(tmp_path, monkeypatch):
    c = cfg(tmp_path)
    path = tmp_path / 'config.json'
    path.write_text(json.dumps(c), encoding='utf-8')
    import urllib.request
    monkeypatch.setattr(urllib.request, 'urlopen', lambda *a, **k: pytest.fail('network attempted'))
    monkeypatch.chdir(tmp_path)
    assert main(['--config', str(path), '--demo', '--offline']) == 0
    folders = list((Path(c['output_directory']) / 'demo').glob('ai-*'))
    assert len(folders) == 3
    assert all((p / 'report.html').exists() and (p / 'telegram_preview.txt').exists() for p in folders)


def test_expired_cache_preserves_previous_report(tmp_path):
    c = cfg(tmp_path)
    s = snapshot()
    state = State(c['state_file'])
    first, _ = create(s, c, state, offline=True)
    old = deepcopy(first)
    old['created_at'] = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    with state.db:
        state.db.execute('UPDATE ai_reports SET value=?', (json.dumps(old),))
    second, reused = create(s, c, state, offline=True)
    assert not reused and second['report_id'] != first['report_id']
    assert state.report(first['report_id']) is not None
    assert create(s, c, state, offline=True)[1]
    state.close()


def test_online_does_not_reuse_offline_fallback(tmp_path, monkeypatch):
    c = cfg(tmp_path)
    c['provider']['enabled'] = True
    s = snapshot()
    s['documents'] = [doc(s)]
    state = State(c['state_file'])
    first, _ = create(s, c, state, offline=True)
    monkeypatch.setenv('OPENAI_API_KEY', 'test-only')
    provider = Mock()
    provider.generate.return_value = (response(), {'input_tokens': 10, 'output_tokens': 20})
    second, reused = create(s, c, state, provider)
    assert not reused and second['mode'] == 'AI_VALIDATED'
    assert first['mode'] == 'QUANTITATIVE_FALLBACK'
    state.close()


def test_ambiguous_symbol_rejected(tmp_path):
    c = cfg(tmp_path)
    records = [{'market': market, 'code': 'DUP', 'score': {}} for market in ('us', 'eu')]
    (tmp_path / 'investment.audit.json').write_text(json.dumps(records))
    with pytest.raises(ValueError, match='market:code'):
        select_records(tmp_path, c, 'DUP')


def test_future_backtest_not_used(tmp_path):
    from ai_reporter.input import backtest_context
    folder = tmp_path / 'future'
    folder.mkdir()
    (folder / 'summary.json').write_text(json.dumps({
        'title': 'actual', 'period': ['2020-01-01', '2026-12-31'],
        'run_at': '2026-12-31T00:00:00+00:00'}))
    assert backtest_context(tmp_path, 'us:X', 'us', '2026-11-03')['status'] == '평가 불가'


def test_provider_has_one_request_per_budget_reservation(monkeypatch):
    from ai_reporter.generation import OpenAIProvider
    from urllib.error import URLError
    monkeypatch.setenv('OPENAI_API_KEY', 'test-only')
    request = Mock(side_effect=URLError('offline'))
    monkeypatch.setattr('ai_reporter.generation.urlopen', request)
    c = load()['provider']
    c['min_interval_seconds'] = 0
    with pytest.raises(RuntimeError):
        OpenAIProvider(c).generate(snapshot())
    assert request.call_count == 1
