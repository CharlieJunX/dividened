from typing import Protocol


class Adapter(Protocol):
    def fetch(self, code, review, as_of):
        """Return facts, metadata, issues; unavailable data must never become zero."""
        ...
