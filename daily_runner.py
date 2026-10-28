"""Collect fresh results and send stocks at or below -30% to Telegram."""
import argparse
import csv
import json
import logging
import os
import sys
import tempfile
import time
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from dotenv import load_dotenv
import stock_52week as stocks
import dividend_report as dividends
from fundamentals import report as fundamentals
from fundamentals.config import load_config as load_fundamental_config
from fundamentals.storage import Store

ROOT = Path(__file__).resolve().parent
KST = timezone(timedelta(hours=9))


def select_alerts(rows):
    selected = []
    for row in rows:
        value = row[stocks.HEADERS[-1]].strip()
        if not value:
            continue
        change = Decimal(value)
        if change.is_finite() and change <= Decimal('-30'):
            selected.append((row['종목코드'], row['회사명'], change))
    return sorted(selected, key=lambda item: item[2])


def build_messages(market, alerts, collected_at):
    if not alerts:
        return []
    header = f'[{market.upper()}] 52주 최고가 대비 -30% 이하\n조회: {collected_at}\nsymbol | 종목명 | 등락률\n'
    messages, current = [], header
    for symbol, name, change in alerts:
        line = f'{symbol} | {name} | {change:.2f}%\n'
        if len((header + line).encode('utf-16-le')) // 2 > 3500:
            raise ValueError('종목명이 Telegram 메시지 제한을 초과합니다.')
        if len((current + line).encode('utf-16-le')) // 2 > 3500:
            messages.append(current.rstrip())
            current = header
        current += line
    messages.append(current.rstrip())
    return messages


def send_message(token, chat_id, text):
    request = Request(
        f'https://api.telegram.org/bot{token}/sendMessage',
        data=json.dumps({'chat_id': chat_id, 'text': text}).encode('utf-8'),
        headers={'Content-Type': 'application/json'}, method='POST')
    try:
        with urlopen(request, timeout=30) as response:
            result = json.load(response)
    except HTTPError as exc:
        # Never print exception URLs: the Telegram URL contains the bot token.
        raise RuntimeError(f'Telegram 전송 실패 (HTTP {exc.code})') from None
    except (URLError, OSError, ValueError):
        # Do not retry an ambiguous send: it may already have been delivered.
        raise RuntimeError('Telegram 전송 결과를 확인할 수 없습니다. 중복 방지를 위해 자동 재전송하지 않습니다.') from None
    if not result.get('ok'):
        raise RuntimeError('Telegram API가 전송 실패를 반환했습니다.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--market', choices=['kr', 'us', 'eu', 'all'], default='all')
    parser.add_argument('--env-file', type=Path, default=ROOT.parent / 'my-stock-agent-starter' / '.env')
    parser.add_argument('--output-dir', type=Path, default=ROOT / 'reports')
    parser.add_argument('--dry-run', action='store_true', help='실제 조회 및 CSV 저장, 전송은 생략')
    parser.add_argument('--config', type=Path, default=ROOT / 'dividend_config.json')
    parser.add_argument('--fundamental-config', type=Path, default=ROOT / 'fundamental_config.json')
    parser.add_argument('--state-dir', type=Path, default=ROOT / 'state')
    parser.add_argument('--no-fundamentals', action='store_true', help='기존 주가·배당 분석만 실행')
    parser.add_argument('--symbols', nargs='+', help='입력 목록 내 지정 코드만 조회 (대표 종목 검증용)')
    args = parser.parse_args()
    config = dividends.load_config(args.config)
    fundamental_config = None if args.no_fundamentals else load_fundamental_config(args.fundamental_config)
    fundamental_enabled = bool(fundamental_config and fundamental_config['enabled'])
    load_dotenv(args.env_file, override=False)
    token, chat_id = os.getenv('TELEGRAM_BOT_TOKEN'), os.getenv('TELEGRAM_CHAT_ID')
    if not args.dry_run and (not token or not chat_id):
        parser.exit(2, 'TELEGRAM_BOT_TOKEN 및 TELEGRAM_CHAT_ID 설정이 필요합니다.\n')
    markets = ['kr', 'us', 'eu'] if args.market == 'all' else [args.market]
    jobs = [(market, stocks.read_stocks(ROOT / f'friendly_{market}.csv', market)) for market in markets]
    if args.symbols:
        unknown = set(args.symbols) - {s[0] for _, inputs in jobs for s in inputs}
        if unknown:
            parser.error(f'입력 목록에 없는 종목코드: {sorted(unknown)}')
        jobs = [(market, [s for s in inputs if s[0] in args.symbols]) for market, inputs in jobs]
        jobs = [(market, inputs) for market, inputs in jobs if inputs]
    args.output_dir.mkdir(parents=True, exist_ok=True)
    log_handler = logging.FileHandler(args.output_dir / 'daily_runner.log', encoding='utf-8')
    log_handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
    logger = logging.getLogger('dividend_report')
    logger.setLevel(logging.INFO)
    logger.addHandler(log_handler)
    status = 0
    # Preview evaluations/deliveries never change the production comparison baseline.
    state = Store(args.state_dir / ('fundamentals-preview.sqlite3' if args.dry_run else 'fundamentals.sqlite3')) if fundamental_enabled else None
    if state:
        state.prune_cache(max(35, fundamental_config['refresh_days'] * 2))
    all_results, preview = [], []
    for market, inputs in jobs:
        # A new folder per market guarantees that yesterday's CSV cannot be sent.
        with tempfile.TemporaryDirectory(dir=args.output_dir) as folder:
            output = Path(folder) / f'friendly_{market}_52week.csv'
            result = stocks.run_market(market, inputs, output)
            status = max(status, result)
            with output.open(encoding='utf-8-sig', newline='') as stream:
                alerts = select_alerts(csv.DictReader(stream))
            for artifact in Path(folder).iterdir():
                (args.output_dir / artifact.name).write_bytes(artifact.read_bytes())
            timestamp = datetime.now(KST).strftime('%Y-%m-%d %H:%M KST')
            messages = build_messages(market, alerts, timestamp)
            quote_audit = json.loads(output.with_suffix('.audit.json').read_text(encoding='utf-8'))
            records = []
            if config['enabled']:
                records, failed = dividends.run_market(market, inputs, quote_audit, args.output_dir,
                                                       config, datetime.now(KST).date())
                all_results.extend(records)
                status = max(status, int(failed))
            if fundamental_enabled:
                financial_records, financial_failed = fundamentals.run_market(
                    market, inputs, quote_audit, records, args.output_dir,
                    fundamental_config, state, datetime.now(KST).date())
                financial_by_code = {r['code']: r for r in financial_records}
                for dividend_record in records:
                    dividend_record['fundamental'] = financial_by_code[dividend_record['code']]
                print(f'{market}: 펀더멘털 {len(financial_records)}개 분석, 자료 수집 오류 {financial_failed}', flush=True)
            print(f'{market}: 알림 대상 {len(alerts)}개, 조회 실패 여부 {bool(result)}', flush=True)
            preview.extend(messages)
            if args.dry_run:
                continue
            for message in messages:
                send_message(token, chat_id, message)
                time.sleep(1.1)
    dividend_messages = dividends.build_messages(all_results, config) if config['enabled'] else []
    preview.extend(dividend_messages)
    pending = fundamentals.notifications(state, [f'{m}:{s[0]}' for m, inputs in jobs for s in inputs],
                                         datetime.now(KST).date()) if state else []
    preview.extend(text for _, text in pending)
    (args.output_dir / 'telegram_preview.txt').write_text('\n\n---\n\n'.join(preview), encoding='utf-8')
    if args.dry_run:
        for message in preview:
            print(message, flush=True)
    else:
        for message in dividend_messages:
            send_message(token, chat_id, message)
            time.sleep(1.1)
        if state:
            def send_financial(text):
                send_message(token, chat_id, text)
                time.sleep(1.1)
            fundamentals.deliver(state, pending, send_financial)
    if state:
        state.close()
    logger.removeHandler(log_handler)
    log_handler.close()
    return status


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (RuntimeError, OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)

