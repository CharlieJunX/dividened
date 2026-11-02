"""Independent event returns; never chain signal returns into a portfolio."""
from datetime import date
from dividend_data import shift_months
from .data import coverage, fx, positive, session_time
from .metrics import mdd


def executable(bar):
    if bar is None:
        return False, 'MISSING_BAR'
    if bar.get('tradable') is False:
        return False, bar.get('status', 'SUSPENDED')
    if not positive(bar.get('open')):
        return False, 'MISSING_OPEN'
    return True, ''


def benchmark(data, s, entry, exit, config, krw=False):
    key = config['benchmarks'].get(s['market'])
    series = data.get('benchmarks', {}).get(key, {}) if key else {}
    if not series or series.get('currency') != s['currency']:
        return None, 'BENCHMARK_MISSING_OR_CURRENCY_MISMATCH'
    bars = {b['date']: b for b in series['bars']}
    a, b = bars.get(entry, {}).get('open'), bars.get(exit, {}).get('open')
    if not positive(a) or not positive(b):
        return None, 'BENCHMARK_EXACT_EXECUTION_DATES_MISSING'
    if krw:
        x, y = fx(data, s['currency'], entry, config['fx_max_age_days']), fx(data, s['currency'], exit, config['fx_max_age_days'])
        if x is None or y is None:
            return None, 'BENCHMARK_FX_MISSING'
        b, a = b * y, a * x
    return (b / a - 1) * 100, series.get('basis', 'UNKNOWN')


def event_trade(data, s, signal, months, config):
    r = {'security': s['id'], 'name': s['name'], 'market': s['market'], 'currency': s['currency'],
         'data_source': s['source'], 'data_version': s['version'], 'retrieved_at': s['retrieved_at'],
         'price_period_end': signal['date'],
         'strategy': signal['strategy'], 'signal_date': signal['date'], 'initial': signal['initial'],
         'segment': signal['segment'], 'months': months, 'common_eligible': signal['common_eligible'],
         'point': signal['point'], 'status': 'NOT_ENTERED', 'reason': '',
         'entry_date': None, 'exit_date': None, 'price_return_pct': None, 'total_return_pct': None,
         'net_return_pct': None, 'krw_net_return_pct': None, 'benchmark_return_pct': None,
         'excess_return_pct': None, 'mdd_pct': None, 'delayed_exit': []}
    end = min(config['end'], signal['segment_end'])
    if not s.get('calendar_verified'):
        r['reason'] = 'UNVERIFIED_EXCHANGE_CALENDAR'
        return r
    sessions = [d for d in s['sessions'] if signal['date'] < d <= end]
    if not sessions:
        r['reason'] = 'NO_NEXT_SESSION_WITHIN_SEGMENT'
        return r
    bars = {b['date']: b for b in s['bars']}
    entry = sessions[0]
    ok, reason = executable(bars.get(entry))
    if not ok:
        r['reason'] = 'ENTRY_' + reason
        return r
    r.update(entry_date=entry, entry_price=bars[entry]['open'])
    target = str(shift_months(date.fromisoformat(entry), months))
    r['target_exit'] = target
    if target > end:
        r.update(status='INCOMPLETE', reason='HORIZON_CROSSES_SEGMENT_END')
        return r
    exit = None
    for d in sessions:
        if d < target:
            continue
        ok, reason = executable(bars.get(d))
        if ok:
            exit = d
            break
        r['delayed_exit'].append({'date': d, 'reason': reason})
    if exit is None:
        r.update(status='UNRESOLVED', reason='NO_EXECUTABLE_EXIT_OR_DELISTING_SETTLEMENT')
        return r
    r.update(exit_date=exit, exit_price=bars[exit]['open'], status='COMPLETE')
    if not coverage(s, 'actions', entry, exit):
        r.update(status='UNRESOLVED', reason='UNVERIFIED_CORPORATE_ACTIONS')
        return r
    quantity, distributions, equity = 1.0, [], [r['entry_price']]
    dividends_known = coverage(s, 'dividends', entry, exit)
    for day in [d for d in s['sessions'] if entry <= d <= exit]:
        if day > entry:
            for split in s.get('splits', []):
                if split['date'] == day:
                    quantity *= split['ratio']
            # Holder before ex-date is entitled even if sold at that day's open.
            for div in s.get('dividends', []):
                if div['date'] == day:
                    distributions.append({'amount': quantity * div['amount'], 'pay_date': div.get('pay_date'), 'ex_date': day})
        price = r['exit_price'] if day == exit else bars.get(day, {}).get('close')
        equity.append(quantity * price + sum(v['amount'] for v in distributions) if positive(price) else None)
    gross_exit = quantity * r['exit_price']
    cash_div = sum(v['amount'] for v in distributions)
    costs = config['costs']
    paid = r['entry_price'] * (1 + costs['buy_slippage']) * (1 + costs['buy_fee'])
    proceeds = gross_exit * (1 - costs['sell_slippage']) * (1 - costs['sell_fee'] - costs['sell_tax'])
    net_div = cash_div * (1 - costs['dividend_withholding'])
    r['price_return_pct'] = (gross_exit / r['entry_price'] - 1) * 100
    r['mdd_pct'] = mdd(equity) if dividends_known else None
    r['dividends'] = distributions
    r['final_shares_per_initial_share'] = quantity
    if dividends_known:
        r['total_return_pct'] = ((gross_exit + cash_div) / r['entry_price'] - 1) * 100
        r['net_return_pct'] = ((proceeds + net_div) / paid - 1) * 100
        buy_fx = fx(data, s['currency'], entry, config['fx_max_age_days'], session_time(s, entry, 'open'))
        sell_fx = fx(data, s['currency'], exit, config['fx_max_age_days'], session_time(s, exit, 'open'))
        div_fx = [fx(data, s['currency'], v['pay_date'], config['fx_max_age_days'])
                  if v['pay_date'] and v['pay_date'] <= end else None for v in distributions]
        if buy_fx and sell_fx and all(v is not None for v in div_fx):
            krw_div = sum(d['amount'] * x for d, x in zip(distributions, div_fx)) * (1 - costs['dividend_withholding'])
            conversion = costs['fx_fee'] if s['currency'] != 'KRW' else 0
            r['krw_net_return_pct'] = ((proceeds * sell_fx + krw_div) * (1 - conversion) /
                                       (paid * buy_fx * (1 + conversion)) - 1) * 100
        else:
            r['krw_reason'] = 'MISSING_FX_OR_UNPAID_DIVIDEND'
    else:
        r['reason'] = 'DIVIDEND_HISTORY_UNVERIFIED:TOTAL_AND_NET_NA'
    bench, basis = benchmark(data, s, entry, exit, config)
    r['benchmark_return_pct'], r['benchmark_basis'] = bench, basis
    if bench is not None:
        # Price-index comparisons use price return; never silently compare total with price.
        comparable = r['total_return_pct'] if basis == 'total_return' else r['price_return_pct'] if basis == 'price' else None
        r['excess_return_pct'] = comparable - bench if comparable is not None else None
        r['excess_basis'] = basis
    return r


def run(data, signals, config, sample='all'):
    securities = {s['id']: s for s in data['securities']}
    rows, occupied = [], {}
    for signal in sorted(signals, key=lambda r: (r['date'], r['security'], r['strategy'])):
        if sample == 'common' and not signal['common_eligible']:
            continue
        for months in config['holding_months']:
            s = securities[signal['security']]
            r = event_trade(data, s, signal, months, config)
            key = (s['id'], signal['strategy'], months, signal['segment'])
            if not config['allow_overlap'] and r['entry_date'] and r['entry_date'] <= occupied.get(key, ''):
                r.update(status='OVERLAP_EXCLUDED', reason='OVERLAPPING_HOLDING_INTERVAL', entry_date=None, exit_date=None)
                for field in ('price_return_pct', 'total_return_pct', 'net_return_pct', 'krw_net_return_pct',
                              'benchmark_return_pct', 'excess_return_pct', 'mdd_pct'):
                    r[field] = None
            elif r['entry_date']:
                occupied[key] = r['exit_date'] or signal['segment_end']
            r['sample'] = sample
            rows.append(r)
    return rows
