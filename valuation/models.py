from dataclasses import dataclass
import math


@dataclass
class Observation:
    value: float | None = None
    reason: str = ''
    evidence: list | None = None
    period_end: str | None = None

    def dump(self):
        value = self.value
        if value is not None and (type(value) not in (int, float) or not math.isfinite(value)):
            raise ValueError('Nonfinite valuation result')
        return {'value': value, 'reason': self.reason, 'evidence': self.evidence or [], 'period_end': self.period_end,
                'eligible': value is not None}
