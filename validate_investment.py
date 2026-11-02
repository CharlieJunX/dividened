"""Read-only reference checks; never sends Telegram or pushes GitHub.

Uses saved dividend price audits; --fetch-missing optionally fetches missing
price histories through the existing Yahoo adapter. Public disclosure values
are explicitly curated in examples/valuation_reference_disclosures.json.
"""
import argparse
from datetime import date
import json
from pathlib import Path
from dotenv import load_dotenv
from fundamentals.models import Fact
from investment.config import load_config
from valuation.metrics import calculate_point
from valuation.history import analyze
from dividend_report import load_config as dividend_config
from dividend_data import fetch_yahoo_history

ROOT = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reports', type=Path, default=ROOT / 'reports')
    parser.add_argument('--output', type=Path, default=ROOT / 'reports/investment_validation.json')
    parser.add_argument('--fetch-missing', action='store_true')
    args = parser.parse_args()
    load_dotenv(ROOT / '.env', override=False)
    config = load_config()
    references = json.loads((ROOT / 'examples/valuation_reference_disclosures.json').read_text(encoding='utf-8'))
    results = []
    for case in references['cases']:
        path = args.reports / f'friendly_{case["market"]}_dividend.audit.json'
        rows = json.loads(path.read_text(encoding='utf-8')) if path.exists() else []
        history = next((r.get('history') for r in rows if r.get('code') == case['code']), None)
        price_cache = args.output.parent / f'reference_prices_{case["market"]}_{case["code"]}.json'
        if history is None and price_cache.exists():
            history = json.loads(price_cache.read_text(encoding='utf-8'))
        if history is None and args.fetch_missing:
            try:
                history = fetch_yahoo_history(case['code'], case['market'], dividend_config(ROOT / 'dividend_config.json'), date.fromisoformat(references['reviewed_on']))
                price_cache.parent.mkdir(parents=True, exist_ok=True)
                price_cache.write_text(json.dumps(history, ensure_ascii=False), encoding='utf-8')
            except Exception:
                pass
        if not history:
            results.append({'code': case['code'], 'status': 'NOT_VERIFIED', 'reason': 'Saved price history unavailable'})
            continue
        facts = []
        for metric, value in case['values'].items():
            instant = metric in {'common_shares', 'common_equity'}
            unit = case['currency'] + '/shares' if metric == 'eps' else 'shares' if metric == 'common_shares' else case['currency']
            facts.append(Fact(metric, value, None if instant else case['start'], case['end'], case['filed'],
                              unit, 'consolidated', case['standard'], case['source'], case['filed'], metric, metric == 'eps'))
        review = {'source': case['source'], 'reviewed_on': references['reviewed_on'],
                  'common_shares_verified': True, 'common_equity_verified': True,
                  'common_earnings_verified': True, 'ffo_definition': 'Reported AFFO available to common stockholders'}
        day = date.fromisoformat(case['valuation_date'])
        point = calculate_point(facts, history, day, case['industry'], config, review)
        price = point['price']['value']
        checks = []
        if price is not None:
            values = case['values']
            expected = {}
            if 'eps' in values:
                expected['per'] = price / values['eps']
            if 'common_equity' in values:
                expected['pbr'] = price * values['common_shares'] / values['common_equity']
                expected['fcf_yield'] = (values['ocf'] - values['capex']) / (price * values['common_shares']) * 100
            if 'affo' in values:
                expected['p_affo'] = price * values['common_shares'] / values['affo']
            for metric, value in expected.items():
                actual = point['metrics'][metric]['value']
                checks.append({'metric': metric, 'actual': actual, 'independent_expected': value,
                               'policy_eligible': point['metrics'][metric].get('eligible'),
                               'passed': actual is not None and abs(actual - value) < 1e-8})
        results.append({'code': case['code'], 'industry': case['industry'], 'valuation_date': str(day),
                        'price': price, 'source': case['source'], 'disclosure_date': case['filed'],
                        'status': 'PASSED' if checks and all(c['passed'] for c in checks) else 'NOT_VERIFIED',
                        'checks': checks, 'notes': case['notes'],
                        'scope': 'Formula/reference-date check only; not a full 60-month ranking validation'})
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
    for result in results:
        print(f'{result["code"]}: {result["status"]}')
    return int(any(r['status'] != 'PASSED' for r in results))


if __name__ == '__main__':
    raise SystemExit(main())
