"""Generate a Korean evidence-linked investment review from existing audits."""
import argparse
import hashlib
import json
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path
from uuid import uuid4
from dotenv import load_dotenv
from ai_reporter.config import load as load_config
from ai_reporter.input import read, select_records, build
from ai_reporter.demo import snapshot as demo_snapshot
from ai_reporter.generation import OpenAIProvider, fallback, validate
from ai_reporter.render import report_object, write, telegram
from ai_reporter.state import State


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False).encode()).hexdigest()


def estimate_usd(snapshot, provider_config):
    # Conservative pre-call budget: 2 chars/token plus full output allocation.
    input_chars = len(json.dumps(snapshot, ensure_ascii=False)) + 1800
    p = provider_config['pricing']
    return ((input_chars / 2) * p['input_per_million_usd'] +
            provider_config['max_output_tokens'] * p['output_per_million_usd']) / 1_000_000


def create(snapshot, config, state, provider=None, offline=False):
    now = datetime.now(timezone.utc)
    p = config['provider']
    signature = digest({'instrument': snapshot['instrument_id'], 'snapshot': snapshot,
                        'policy': {'risk': snapshot['risk_status'], 'evaluation': snapshot['evaluation_status']},
                        'prompt': p['prompt_version'], 'model': p['model'], 'settings': p,
                        'documents': [(d['id'], d['version'], d['sha256']) for d in snapshot['documents']],
                        'execution': {'offline': offline, 'enabled': config['enabled']}})
    cached = state.cached(signature)
    if cached:
        age_days = (now - datetime.fromisoformat(cached['created_at'])).total_seconds() / 86400
        maximum = min(config['cache_days'].values())
        if age_days <= maximum:
            return cached, True
    usage, mode, problems = {'input_tokens': None, 'output_tokens': None, 'estimated_usd': 0}, 'QUANTITATIVE_FALLBACK', []
    narrative = None
    if not offline and config['enabled'] and p['enabled']:
        if not os.getenv('OPENAI_API_KEY'):
            problems.append('OPENAI_API_KEY_MISSING')
        else:
            for _ in range(min(p['retries'], 2)):
                estimate = estimate_usd(snapshot, p)
                call_id = state.reserve(snapshot['instrument_id'], p, estimate)
                if call_id is None:
                    problems.append('AI_CALL_BUDGET_EXCEEDED')
                    break
                usage['estimated_usd'] += estimate
                billed = {}
                try:
                    raw, billed = (provider or OpenAIProvider(p)).generate(snapshot)
                    for key in ('input_tokens', 'output_tokens'):
                        if billed.get(key) is not None:
                            usage[key] = (usage[key] or 0) + billed[key]
                    usage['model'] = billed.get('model', p['model'])
                    state.finish_call(call_id, billed, 'RECEIVED')
                    checked, rejected = validate(raw, snapshot)
                    problems.extend(rejected)
                    if not rejected and (sum(len(checked[k]) for k in ('investment_thesis', 'counterarguments', 'related_events')) or not snapshot['documents']):
                        narrative, mode = checked, 'AI_VALIDATED'
                    elif not rejected:
                        problems.append('NO_VERIFIED_CLAIMS')
                    state.finish_call(call_id, billed, 'SUCCESS' if narrative else 'REJECTED')
                    if narrative:
                        break
                except (ValueError, RuntimeError, KeyError, TypeError, OSError, TimeoutError) as exc:
                    problems.append('AI_VALIDATION_OR_PROVIDER_FAILURE:' + str(exc)[:80])
                    state.finish_call(call_id, billed, 'FAILED_OR_UNCERTAIN')
    if narrative is None:
        narrative = fallback(snapshot, 'AI 생성·검증 불가: 정량 자료 중심 보고서' if problems else 'AI 사용 설정 없음 또는 오프라인 모드')
    else:
        # Free-form model sections could assert unsupported facts. Keep only validated cited items.
        safe = fallback(snapshot, '공식 자료와 기존 정량 분석의 범위 안에서 검토')
        for key in ('summary', 'scenarios', 'monitoring_items', 'limitations'):
            narrative[key] = safe[key]
    report_id = 'ai-' + now.strftime('%Y%m%dT%H%M%S') + '-' + signature[:12] + '-' + uuid4().hex[:8]
    report = report_object(snapshot, narrative, report_id, now.isoformat(), mode, usage,
                           {'valid': True, 'model_claims_rejected': problems,
                            'documents_rejected': snapshot['document_rejections']})
    folder = Path(config['output_directory']) / report_id
    write(folder, report, config)
    state.save(report, folder, signature)
    return report, False


def send(report_id, config, state, retry_uncertain=False):
    report = state.report(report_id)
    if not report or not report['validation']['valid']:
        raise ValueError('검증 완료된 보고서 ID가 필요합니다')
    previous = state.delivery(report_id)
    if previous == 'SENT':
        return 'ALREADY_SENT'
    if previous in ('ATTEMPTED', 'UNCERTAIN') and not retry_uncertain:
        raise ValueError('전송 결과 불확실: 수동 확인 후 --retry-uncertain 사용')
    token, chat = os.getenv('TELEGRAM_BOT_TOKEN'), os.getenv('TELEGRAM_CHAT_ID')
    if not token or not chat:
        raise ValueError('TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID 설정 필요')
    from daily_runner import send_message
    content = telegram(report, config['telegram']['maximum_utf16_units'])
    state.mark_delivery(report_id, 'ATTEMPTED')
    try:
        send_message(token, chat, content)
    except Exception:
        state.mark_delivery(report_id, 'UNCERTAIN')
        raise
    state.mark_delivery(report_id, 'SENT')
    return 'SENT'


def main(argv=None, provider=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path)
    parser.add_argument('--results-dir', type=Path, default=Path('reports'))
    parser.add_argument('--symbol', help='기존 종목 ID market:code 또는 유일한 종목코드')
    parser.add_argument('--candidates', action='store_true')
    parser.add_argument('--top', type=int)
    parser.add_argument('--dry-run', action='store_true', help='Telegram 전송 없음; 설정에 따라 AI API 사용')
    parser.add_argument('--use-ai', action='store_true', help='AI API 생성 활성화; OPENAI_API_KEY 필요')
    parser.add_argument('--offline', action='store_true', help='네트워크 사용 없음')
    parser.add_argument('--demo', action='store_true', help='가상 기업·수치')
    parser.add_argument('--fetch-docs', action='store_true', help='명시된 SEC/DART 식별자로 공식 공시 목록 조회')
    parser.add_argument('--report-id')
    parser.add_argument('--send-telegram', action='store_true')
    parser.add_argument('--retry-uncertain', action='store_true')
    parser.add_argument('--env-file', type=Path, default=Path('.env'))
    args = parser.parse_args(argv)
    if args.send_telegram and not args.report_id or args.send_telegram and (args.offline or args.dry_run or args.demo):
        parser.error('--send-telegram requires --report-id and excludes --offline/--dry-run/--demo')
    if args.fetch_docs and args.offline:
        parser.error('--fetch-docs cannot be offline')
    if args.top is not None and args.top < 1:
        parser.error('--top must be positive')
    config = load_config(args.config)
    if args.use_ai:
        config['provider']['enabled'] = True
    if args.demo:
        config['state_file'] = 'state/ai_reporter-demo.sqlite3'
        config['output_directory'] = str(Path(config['output_directory']) / 'demo')
    load_dotenv(args.env_file, override=False)
    state = State(config['state_file'])
    try:
        if args.send_telegram:
            print(send(args.report_id, config, state, args.retry_uncertain))
            return 0
        if args.report_id:
            report = state.report(args.report_id)
            if report is None:
                parser.error('Unknown report ID')
            print(str(Path(config['output_directory']) / args.report_id / 'report.html'))
            return 0
        if args.demo:
            snapshots = [demo_snapshot(m) for m in ('kr', 'us', 'eu')]
        else:
            if not (args.symbol or args.candidates or config['selected_symbols']):
                parser.error('--symbol, --candidates, or configured selected_symbols required')
            records = select_records(args.results_dir, config, args.symbol, args.candidates, args.top)
            snapshots = [build(r, args.results_dir, config) for r in records]
        if args.fetch_docs and snapshots:
            from ai_reporter.official import fetch
            from ai_reporter.evidence import validate_document
            discovered, errors = fetch(config, snapshots)
            for s in snapshots:
                existing = {d['id'] for d in s['documents']}
                for doc in discovered:
                    if doc['instrument_id'] != s['instrument_id'] or doc['id'] in existing:
                        continue
                    reason = validate_document(doc, s['instrument_id'], s['as_of'])
                    if reason:
                        s['document_rejections'].append({'id': doc['id'], 'reason': reason})
                    else:
                        s['documents'].append(doc)
                        existing.add(doc['id'])
                s['document_rejections'].extend(errors)
        for s in snapshots[:config['max_reports_per_run']]:
            report, reused = create(s, config, state, provider, args.offline or args.demo)
            print(('재사용: ' if reused else '생성: ') + str(Path(config['output_directory']) / report['report_id'] / 'report.html'))
        return 0
    finally:
        state.close()


if __name__ == '__main__':
    raise SystemExit(main())
