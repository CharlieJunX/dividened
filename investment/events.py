def detect(current, previous, config):
    if previous is None:
        return []
    events = []
    old, new = previous['score'], current['score']
    if new['grade'] == 'S' and old['grade'] != 'S':
        events.append('A_TO_S' if old['grade'] == 'A' else 'S_ENTRY')
    if (old['adjusted_score'] is not None and new['adjusted_score'] is not None
            and old['adjusted_score'] - new['adjusted_score'] >= config['notifications']['score_drop']):
        events.append('SCORE_DROP')
    if current['fundamental_status'] == 'HIGH_RISK' and previous['fundamental_status'] != 'HIGH_RISK':
        events.append('HIGH_RISK_ENTRY')
    if (previous['candidate'] and current['candidate'] and current['valuation']['historically_cheap']
            and not previous['valuation']['historically_cheap']):
        events.append('DIVIDEND_AND_VALUE_ENTRY')
    return events
