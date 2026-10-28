import json
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from .storage import digest
from .models import DataError


class Client:
    def __init__(self, store, config):
        self.store, self.config, self.last = store, config, 0.0

    def json(self, url, ttl=86400, headers=None, namespace=''):
        # URLs containing API keys are hashed, never persisted or exposed in errors.
        key = 'http:' + digest([url, namespace])
        cached = self.store.get(key, ttl)
        if cached is not None:
            return cached
        for attempt in range(self.config['attempts']):
            time.sleep(max(0, self.config['interval_seconds'] - (time.monotonic() - self.last)))
            self.last = time.monotonic()
            try:
                req = Request(url, headers=headers or {'User-Agent': 'fundamental-monitor/1.0'})
                with urlopen(req, timeout=self.config['timeout']) as response:
                    value = json.load(response)
                if not isinstance(value, dict):
                    raise ValueError('Expected JSON object')
                # API error responses must not poison a long-lived cache.
                if value.get('status') not in (None, '000', '013'):
                    raise ValueError('Provider rejected request (credentials or quota)')
                self.store.put(key, value)
                return value
            except HTTPError as exc:
                if exc.code not in (429, 500, 502, 503, 504):
                    raise DataError(f'Financial provider HTTP {exc.code}') from None
                retry = exc.headers.get('Retry-After', '') if exc.headers else ''
                delay = min(float(retry), 30) if retry.isdigit() else 2 ** attempt
            except (OSError, ValueError):
                delay = 2 ** attempt
            if attempt + 1 < self.config['attempts']:
                time.sleep(delay)
        raise DataError('Financial provider unavailable; timeout, response or quota error') from None
