"""No calendar-year assumptions in SEC periods; cumulative values are not quarters."""
from dataclasses import replace
from datetime import date, timedelta


def versions(facts, as_of):
    selected = {}
    for f in facts:
        if f.filed > str(as_of) or f.end > str(as_of):
            continue
        key = f.metric, f.start, f.end, f.basis
        previous = selected.get(key)
        if previous is None or (f.filed, f.accession) > (previous.filed, previous.accession):
            selected[key] = f
        elif (f.filed, f.accession) == (previous.filed, previous.accession) and f.value != previous.value:
            raise ValueError('Conflicting facts for the same period and filing')
    return list(selected.values())


def quarters(facts):
    result = {(f.start, f.end, f.basis): f for f in facts if 70 <= f.days <= 110}
    for current in facts:
        if not 150 <= current.days <= 380 or current.metric == 'eps':
            continue
        previous = [f for f in facts if f.start == current.start and f.end < current.end
                    and f.basis == current.basis and f.tag == current.tag
                    and 70 <= (date.fromisoformat(current.end) - date.fromisoformat(f.end)).days <= 110]
        if not previous:
            continue
        prior = max(previous, key=lambda f: f.end)
        start = (date.fromisoformat(prior.end) + timedelta(days=1)).isoformat()
        key = start, current.end, current.basis
        if key not in result:
            result[key] = replace(current, start=start, value=current.value - prior.value,
                                  filed=max(current.filed, prior.filed),
                                  accession=current.accession + ' minus ' + prior.accession,
                                  source=current.source + ' ; derived using ' + prior.source)
    return sorted(result.values(), key=lambda f: f.end)


def annuals(facts):
    return sorted((f for f in facts if 330 <= f.days <= 380), key=lambda f: f.end)


def consecutive(items):
    return all(date.fromisoformat(b.start) == date.fromisoformat(a.end) + timedelta(days=1)
               and a.basis == b.basis for a, b in zip(items, items[1:]))


def ttm_pair(facts, anchor=None):
    qs = quarters(facts)
    if anchor:
        qs = [q for q in qs if q.end <= anchor]
    if len(qs) < 8:
        return None
    recent = qs[-8:]
    if not consecutive(recent) or len({q.tag for q in recent}) != 1 or (anchor and recent[-1].end != anchor):
        return None
    if recent[0].metric == 'eps' and not all(q.eps_verified for q in recent):
        return None
    return sum(q.value for q in recent[-4:]), sum(q.value for q in recent[:4]), recent


def growth(current, prior):
    if prior <= 0:
        return None
    return (current / prior - 1) * 100


def ttm_window(facts, anchor=None):
    qs = quarters(facts)
    if anchor:
        qs = [q for q in qs if q.end <= anchor]
    recent = qs[-4:]
    if len(recent) != 4 or not consecutive(recent) or len({q.tag for q in recent}) != 1:
        return None
    if anchor and recent[-1].end != anchor:
        return None
    if recent[0].metric == 'eps' and not all(q.eps_verified for q in recent):
        return None
    return sum(q.value for q in recent), recent
