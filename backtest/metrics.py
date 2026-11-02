from datetime import date
from math import sqrt
from statistics import mean, median, stdev
from valuation.history import quantile


def mdd(values):
    if not values or any(v is None or v <= 0 for v in values):
        return None
    peak, worst = values[0], 0.0
    for value in values:
        peak = max(peak, value)
        worst = min(worst, value / peak - 1)
    return worst * 100


def event_summary(rows, min_sample):
    completed = [r for r in rows if r['status'] == 'COMPLETE']
    returns = [r['net_return_pct'] for r in completed if r.get('net_return_pct') is not None]
    excess = [r['excess_return_pct'] for r in completed if r.get('excess_return_pct') is not None]
    reasons = {}
    for r in rows:
        if r.get('reason'):
            reasons[r['reason']] = reasons.get(r['reason'], 0) + 1
    return {'signals': len(rows), 'entries': sum(bool(r.get('entry_date')) for r in rows),
            'complete': len(completed), 'net_evaluable': len(returns),
            'incomplete': sum(r['status'] == 'INCOMPLETE' for r in rows),
            'unresolved': sum(r['status'] == 'UNRESOLVED' for r in rows),
            'overlap_excluded': sum(r['status'] == 'OVERLAP_EXCLUDED' for r in rows),
            'excluded_reasons': reasons, 'small_sample': len(returns) < min_sample,
            'mean_net_pct': mean(returns) if returns else None,
            'median_net_pct': median(returns) if returns else None,
            'q25_net_pct': quantile(returns, .25), 'q75_net_pct': quantile(returns, .75),
            'win_rate_pct': 100 * sum(r > 0 for r in returns) / len(returns) if returns else None,
            'benchmark_comparisons': len(excess),
            'benchmark_win_rate_pct': 100 * sum(r > 0 for r in excess) / len(excess) if excess else None,
            'mean_excess_pct': mean(excess) if excess else None}


def portfolio_metrics(curve, config, turnover):
    result = dict.fromkeys(['total_return_pct', 'cagr_pct', 'volatility_pct', 'sharpe', 'mdd_pct',
                            'turnover', 'average_cash_weight_pct'])
    if len(curve) < 2 or any(r.get('nav') is None or r['nav'] <= 0 for r in curve):
        return {**result, 'reason': 'INCOMPLETE_DAILY_NAV'}
    values = [r['nav'] for r in curve]
    days = (date.fromisoformat(curve[-1]['date']) - date.fromisoformat(curve[0]['date'])).days
    if days <= 0:
        return {**result, 'reason': 'INSUFFICIENT_DAILY_NAV'}
    returns = [b / a - 1 for a, b in zip(values, values[1:])]
    vol = stdev(returns) if len(returns) > 1 else None
    rf = (1 + config['risk_free_rate']) ** (1 / config['annualization_days']) - 1
    result.update(total_return_pct=(values[-1] / values[0] - 1) * 100,
                  cagr_pct=((values[-1] / values[0]) ** (365.25 / days) - 1) * 100,
                  volatility_pct=vol * sqrt(config['annualization_days']) * 100 if vol is not None else None,
                  sharpe=(mean(returns) - rf) / vol * sqrt(config['annualization_days']) if vol else None,
                  mdd_pct=mdd(values), turnover=turnover / mean(values),
                  average_cash_weight_pct=mean(r['cash'] / r['nav'] for r in curve) * 100,
                  reason='')
    return result
