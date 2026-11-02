"""Cash/position ledger. Each strategy, horizon, sample and segment starts independently."""
from datetime import date, datetime, time, timezone
from math import floor
from bisect import bisect_right
from zoneinfo import ZoneInfo
from .data import coverage, fx, positive, close_time, session_time, available_day
from .events import executable, event_trade
from .metrics import portfolio_metrics


def open_time(s, day):
    if not s.get('open_time'):
        raise ValueError('Portfolio requires verified exchange open_time')
    return session_time(s, day, 'open')


def run_one(data, signals, config, strategy, months, segment, sample):
    securities = {s['id']: s for s in data['securities']}
    bars = {key: {b['date']: b for b in s['bars']} for key, s in securities.items()}
    timelines, close_calendars, schedules = {}, {}, {}
    for key, s in securities.items():
        observations, closes = [], []
        for d in s['sessions']:
            opening, closing = open_time(s, d), close_time(s, d)
            schedules.setdefault(d, []).append((opening, key))
            closes.append((closing, d))
            b = bars[key].get(d, {})
            for field, moment in [('open', opening), ('close', closing)]:
                if positive(b.get(field)):
                    observations.append((moment, b[field]))
        observations.sort()
        closes.sort()
        timelines[key] = ([t for t, _ in observations], [p for _, p in observations])
        close_calendars[key] = ([t for t, _ in closes], [d for _, d in closes])
    cash, distributions, positions, receivables = config['initial_capital'], 0.0, {}, []
    ledger, curve, requests, turnover = [], [], {}, 0.0
    costs = config['costs']
    for signal in signals:
        if signal['strategy'] != strategy or signal['segment'] != segment['name'] or sample == 'common' and not signal['common_eligible']:
            continue
        trade = event_trade(data, securities[signal['security']], signal, months, config)
        if trade['entry_date']:
            requests.setdefault(trade['entry_date'], []).append(trade)
        else:
            ledger.append({'date': signal['date'], 'security': signal['security'], 'action': 'SKIP', 'reason': trade['reason']})
    days = sorted({d for s in securities.values() for d in s['sessions'] if segment['start'] <= d <= segment['end']})
    start = segment['start']
    curve.append({'date': start, 'phase': 'initial', 'nav': cash, 'cash': cash, 'investable_cash': cash,
                  'positions_value': 0, 'receivables': 0})
    uncertain = set()

    def exchange(currency, day, at=None):
        if config['base_currency'] == currency:
            return 1.0
        if config['base_currency'] != 'KRW':
            return None
        return fx(data, currency, day, config['fx_max_age_days'], at)

    def valuation(day, at=None):
        value, receivable_value = 0.0, 0.0
        if uncertain:
            return None, None
        for key, pos in positions.items():
            s = securities[key]
            if day > s.get('calendar_through', s['sessions'][-1]):
                return None, None
            # Only prices already observed by this order timestamp may size a new purchase.
            cutoff = at if at is not None else close_time(s, day)
            close_moments, close_dates = close_calendars[key]
            index = bisect_right(close_moments, cutoff) - 1
            latest_close = close_dates[index] if index >= 0 else None
            if latest_close and latest_close >= pos['entry_date'] and not positive(bars[key].get(latest_close, {}).get('close')):
                return None, None
            moments, prices = timelines[key]
            index = bisect_right(moments, cutoff) - 1
            rate = exchange(s['currency'], day, at)
            if index < 0 or rate is None:
                return None, None
            value += pos['shares'] * prices[index] * rate
        for rec in receivables:
            rate = exchange(rec['currency'], day, at)
            if rate is None:
                return None, None
            receivable_value += rec['amount'] * rate
        return value, receivable_value

    for day in days:
        schedule = sorted(schedules[day])
        actions_done = set()
        # Market-open order, then fixed identifier; no score-based tie breaking.
        for moment, key in schedule:
            # Apply all same-instant actions before any order sizes its NAV.
            for action_moment, action_key in schedule:
                if action_moment > moment or action_key in actions_done:
                    continue
                actions_done.add(action_key)
                if action_key not in positions:
                    continue
                action_security, pos = securities[action_key], positions[action_key]
                for split in action_security.get('splits', []):
                    if split['date'] == day and day > pos['entry_date']:
                        pos['shares'] *= split['ratio']
                        ledger.append({'date': day, 'security': action_key, 'action': 'SPLIT', 'shares': pos['shares']})
                for div in action_security.get('dividends', []):
                    if div['date'] == day and day > pos['entry_date']:
                        public = available_day(div, action_security)
                        if public is None or public > day:
                            uncertain.add(action_key)
                            ledger.append({'date': day, 'security': action_key, 'action': 'UNRESOLVED_ENTITLEMENT',
                                           'reason': 'DIVIDEND_AMOUNT_NOT_VERIFIED_AT_EX_DATE'})
                            continue
                        amount = pos['shares'] * div['amount'] * (1 - costs['dividend_withholding'])
                        receivables.append({'security': action_key, 'currency': action_security['currency'], 'amount': amount,
                                            'pay_date': div.get('pay_date'), 'ex_date': day})
                        ledger.append({'date': day, 'security': action_key, 'action': 'ENTITLEMENT', 'local_amount': amount})
            s, bar = securities[key], bars[key].get(day)
            if key in positions:
                pos = positions[key]
                if not coverage(s, 'actions', pos['entry_date'], day) or not coverage(s, 'dividends', pos['entry_date'], day):
                    uncertain.add(key)
                if day >= pos['target_exit']:
                    ok, reason = executable(bar)
                    rate = exchange(s['currency'], day, moment)
                    if ok and rate is not None:
                        gross = pos['shares'] * bar['open'] * rate
                        conversion = costs['fx_fee'] if s['currency'] != config['base_currency'] else 0
                        proceeds = gross * (1 - costs['sell_slippage']) * (1 - costs['sell_fee'] - costs['sell_tax']) * (1 - conversion)
                        cash += proceeds
                        turnover += gross
                        ledger.append({'date': day, 'security': key, 'action': 'SELL', 'shares': pos['shares'],
                                       'price': bar['open'], 'fx': rate, 'cash_flow': proceeds, 'cash': cash,
                                       'delayed': day > pos['target_exit']})
                        del positions[key]
                    else:
                        ledger.append({'date': day, 'security': key, 'action': 'EXIT_DELAYED', 'reason': reason or 'MISSING_FX'})
            for trade in sorted((t for t in requests.get(day, []) if t['security'] == key), key=lambda t: t['security']):
                reason = ''
                if key in positions:
                    reason = 'ALREADY_HELD'
                elif len(positions) >= config['max_positions']:
                    reason = 'POSITION_LIMIT'
                rate = exchange(s['currency'], day, moment)
                pv, rv = valuation(day, moment)
                if rate is None:
                    reason = reason or 'MISSING_FX'
                if pv is None:
                    reason = reason or 'NAV_UNAVAILABLE'
                if not reason:
                    nav = cash + distributions + pv + rv
                    target = nav * config['target_weight']
                    conversion = costs['fx_fee'] if s['currency'] != config['base_currency'] else 0
                    per_share = trade['entry_price'] * rate * (1 + costs['buy_slippage']) * (1 + costs['buy_fee']) * (1 + conversion)
                    shares = target / per_share if config['fractional_shares'] else floor(target / per_share)
                    paid = shares * per_share
                    if target > cash + 1e-8 or shares <= 0:
                        reason = 'INSUFFICIENT_CASH'
                    else:
                        cash -= paid
                        turnover += shares * trade['entry_price'] * rate
                        positions[key] = {'shares': shares, 'entry_date': day, 'target_exit': trade['target_exit']}
                        ledger.append({'date': day, 'security': key, 'action': 'BUY', 'shares': shares,
                                       'price': trade['entry_price'], 'fx': rate, 'cash_flow': -paid, 'cash': cash,
                                       'nav_before': nav, 'target': target})
                if reason:
                    ledger.append({'date': day, 'security': key, 'action': 'SKIP', 'reason': reason})
        # Payment dates without intraday time: credit after all orders that day.
        for rec in list(receivables):
            if rec['pay_date'] and rec['pay_date'] <= day:
                rate = exchange(rec['currency'], rec['pay_date'])
                if rate is not None:
                    conversion = costs['fx_fee'] if rec['currency'] != config['base_currency'] else 0
                    amount = rec['amount'] * rate * (1 - conversion)
                    distributions += amount  # Held separately: default no dividend reinvestment.
                    receivables.remove(rec)
                    ledger.append({'date': day, 'payment_date': rec['pay_date'], 'security': rec['security'],
                                   'action': 'DIVIDEND_PAID', 'cash_flow': amount, 'fx': rate})
        pv, rv = valuation(day)
        nav = cash + distributions + pv + rv if pv is not None else None
        curve.append({'date': day, 'phase': 'close', 'nav': nav, 'cash': cash + distributions,
                      'investable_cash': cash, 'positions_value': pv, 'receivables': rv})
    # Initial and same-day close are retained in the audit; statistics use one daily interval.
    stats_curve = [curve[0]] + [r for r in curve[1:] if r['date'] > curve[0]['date']]
    result = {'strategy': strategy, 'months': months, 'segment': segment['name'], 'sample': sample,
              'currency': config['base_currency'], 'metrics': portfolio_metrics(stats_curve, config, turnover),
              'curve': curve, 'ledger': ledger, 'open_positions': positions, 'unpaid_dividends': receivables,
              'uncertain_securities': sorted(uncertain), 'benchmark_return_pct': None,
              'benchmark_reason': 'Global portfolio benchmark allocation is not defined; use event matched benchmarks'}
    return result


def run(data, signals, config, evaluations=None):
    segments = config['segments'] or [{'name': 'all', 'start': config['start'], 'end': config['end']}]
    output = []
    for sample in ('all', 'common'):
        for segment in segments:
            for s in config['strategies']:
                for m in config['holding_months']:
                    result = run_one(data, signals, config, s['id'], m, segment, sample)
                    valid = [r for r in (evaluations or []) if r['strategy'] == s['id'] and r['segment'] == segment['name']
                             and r['condition'] is not None and (sample != 'common' or r['common_eligible'])]
                    failures = [r for r in result['ledger'] if r['action'] == 'SKIP' and r['reason'] not in
                                {'INSUFFICIENT_CASH', 'POSITION_LIMIT', 'ALREADY_HELD'}]
                    if (evaluations is not None and not valid) or failures:
                        result['metrics'] = {**dict.fromkeys(result['metrics']), 'reason': 'STRATEGY_OR_EXECUTION_DATA_UNAVAILABLE'}
                    output.append(result)
    return output
