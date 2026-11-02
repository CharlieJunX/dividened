import csv
import html
import json
import subprocess
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
from .data import fingerprint
from .metrics import event_summary


def csv_file(path, rows):
    fields = list(dict.fromkeys(k for r in rows for k in r)) or ['status']
    with Path(path).open('w', encoding='utf-8-sig', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: json.dumps(v, ensure_ascii=False, allow_nan=False) if isinstance(v, (dict, list)) else v for k, v in row.items()})


def json_file(path, data):
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')


def save(data, config, policies, evaluations, events, portfolios, errors=()):
    now = datetime.now(timezone.utc)
    folder = Path(config['output_dir']) / (now.strftime('%Y%m%dT%H%M%S') + '_' + uuid4().hex[:8])
    folder.mkdir(parents=True)
    try:
        commit = subprocess.run(['git', 'rev-parse', 'HEAD'], capture_output=True, text=True, timeout=5).stdout.strip() or None
    except (OSError, subprocess.TimeoutExpired):
        commit = None
    root = Path(__file__).resolve().parent.parent
    files = list(root.glob('*.py')) + [p for directory in ('backtest', 'fundamentals', 'investment', 'valuation')
                                    for p in (root / directory).rglob('*.py')]
    code_hash = fingerprint({str(p.relative_to(root)): p.read_text(encoding='utf-8') for p in files})
    groups = defaultdict(list)
    for r in events:
        for market, year in [('ALL', 'ALL'), (r['market'], 'ALL'), ('ALL', r['signal_date'][:4])]:
            groups[(r['sample'], r['strategy'], r['months'], r['segment'], market, year)].append(r)
    for sample in ('all', 'common'):
        for strategy in config['strategies']:
            for months in config['holding_months']:
                for segment in config['segments'] or [{'name': 'all'}]:
                    groups.setdefault((sample, strategy['id'], months, segment['name'], 'ALL', 'ALL'), [])
    summaries = []
    for key, rows in groups.items():
        eligible = [r for r in evaluations if r['strategy'] == key[1] and r['segment'] == key[3]
                    and r['condition'] is not None and (key[0] != 'common' or r['common_eligible'])]
        summaries.append({**dict(zip(('sample', 'strategy', 'months', 'segment', 'market', 'signal_year'), key)),
                          'evaluation_status': 'EVALUABLE' if eligible else 'UNAVAILABLE',
                          **event_summary(rows, config['min_sample'])})
    support = []
    for strategy in config['strategies']:
        rows = [r for r in evaluations if r['strategy'] == strategy['id']]
        valid = sum(r['condition'] is not None for r in rows)
        reasons = defaultdict(int)
        for r in rows:
            if r['reason']:
                reasons[r['reason']] += 1
        support.append({'strategy': strategy['id'], 'status': 'EVALUABLE' if valid else 'UNAVAILABLE',
                        'observations': len(rows), 'valid': valid, 'coverage_pct': valid / len(rows) * 100 if rows else None,
                        'common_observations': sum(r['common_eligible'] for r in rows), 'missing_reasons': dict(reasons)})
    title = '가상 데이터 데모 — 실제 투자 성과 아님' if data.get('synthetic') else (
        '현재 관심종목의 과거 분석 — 생존편향 포함' if config['universe_mode'] == 'current_watchlist' else '과거 편입 이력 기준 분석')
    summary = {'title': title, 'run_at': now.isoformat(), 'code_commit': commit, 'code_sha256': code_hash,
               'data_sha256': fingerprint(data), 'experiment': config['experiment'], 'period': [config['start'], config['end']],
               'sources': [{'security': s['id'], 'source': s['source'], 'version': s['version'], 'retrieved_at': s['retrieved_at'],
                            'calendar_verified': s.get('calendar_verified', False)} for s in data['securities']],
               'strategy_support': support, 'event_summaries': summaries,
               'portfolio_summaries': [{k: v for k, v in p.items() if k not in ('curve', 'ledger')} for p in portfolios],
               'collection_errors': list(errors), 'warnings': [
                   'Event returns are not portfolio CAGR. Overlapping/correlated observations are not independent.',
                   'No automatic parameter selection. Final-test results must not be used to select parameters.',
                   'Imported historical versions and calendar completeness require independent verification.',
                   'Current registered grades are never retroactively assigned.', config['cost_basis']],
               'overlap_allowed': config['allow_overlap']}
    json_file(folder / 'summary.json', summary)
    json_file(folder / 'config.json', config)
    json_file(folder / 'policies.json', policies)
    # Content snapshot makes the run reproducible even after the input cache is updated.
    json_file(folder / 'input.json', data)
    csv_file(folder / 'signals.csv', evaluations)
    csv_file(folder / 'events.csv', events)
    csv_file(folder / 'comparison.csv', summaries)
    csv_file(folder / 'strategy_support.csv', support)
    for i, p in enumerate(portfolios):
        csv_file(folder / f'portfolio_{i:03d}_ledger.csv', p['ledger'])
        csv_file(folder / f'portfolio_{i:03d}_nav.csv', p['curve'])
    def table(rows, columns):
        return '<table><tr>' + ''.join('<th>' + html.escape(c) + '</th>' for c in columns) + '</tr>' + ''.join(
            '<tr>' + ''.join('<td>' + html.escape('N/A' if r.get(c) is None else str(r[c])) + '</td>' for c in columns) + '</tr>' for r in rows) + '</table>'
    content = '<!doctype html><html lang="ko"><meta charset="utf-8"><title>백테스트</title><style>body{font:16px sans-serif;max-width:1400px;margin:32px auto;padding:16px;color:#183044}table{border-collapse:collapse;margin:24px 0}td,th{border:1px solid #ccc;padding:8px}th{background:#eef3f8}h1{font-size:26px}</style><h1>' + html.escape(title) + '</h1>'
    content += '<p>전체 표본과 모든 비교 전략의 입력이 있는 공통 표본을 별도로 표시합니다. 수익률 단위는 %. N/A는 0%가 아닙니다.</p>'
    content += table(support, ['strategy', 'status', 'observations', 'valid', 'coverage_pct', 'common_observations'])
    content += table(summaries, ['sample', 'strategy', 'months', 'segment', 'market', 'signal_year', 'signals', 'entries', 'complete', 'net_evaluable', 'mean_net_pct', 'win_rate_pct', 'small_sample'])
    content += '<h2>포트폴리오</h2>' + table([{'strategy': p['strategy'], 'months': p['months'], 'segment': p['segment'], 'sample': p['sample'], **p['metrics']} for p in portfolios], ['strategy', 'months', 'segment', 'sample', 'total_return_pct', 'cagr_pct', 'sharpe', 'mdd_pct', 'reason'])
    content += '<h2>가정과 한계</h2><ul>' + ''.join('<li>' + html.escape(w) + '</li>' for w in summary['warnings']) + '</ul></html>'
    (folder / 'report.html').write_text(content, encoding='utf-8')
    (folder / 'telegram_preview.txt').write_text(title + '\n' + '\n'.join(f"{r['strategy']}: {r['status']} / 데이터 충족률 {r['coverage_pct']}%" for r in support), encoding='utf-8')
    return folder
