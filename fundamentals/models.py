"""Normalized facts are serialized without dropping filing/version evidence."""
from dataclasses import dataclass, asdict
from datetime import date
import math


class DataError(ValueError):
    """Sanitized actionable provider/data error, safe to include in audit output."""


@dataclass(frozen=True)
class Fact:
    metric: str
    value: float
    start: str | None
    end: str
    filed: str
    unit: str
    scope: str
    standard: str
    source: str
    accession: str
    tag: str
    eps_verified: bool = False

    def __post_init__(self):
        if type(self.value) not in (int, float) or not math.isfinite(self.value):
            raise ValueError('Nonfinite or missing financial amount')
        if type(self.eps_verified) is not bool:
            raise ValueError('EPS verification must be boolean')
        if date.fromisoformat(self.end) > date.fromisoformat(self.filed):
            raise ValueError('Period ends after filing')
        if self.start and date.fromisoformat(self.start) > date.fromisoformat(self.end):
            raise ValueError('Invalid financial period')
        if not all((self.unit, self.scope, self.standard, self.source, self.accession, self.tag)):
            raise ValueError('Missing fact provenance')

    def dump(self):
        return asdict(self)

    @property
    def basis(self):
        return self.unit, self.scope, self.standard

    @property
    def days(self):
        return (date.fromisoformat(self.end) - date.fromisoformat(self.start)).days + 1 if self.start else 0
