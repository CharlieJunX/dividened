"""Durable SQLite state, append-only evaluations, independent delivery receipts."""
import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


class Store:
    def __init__(self, path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS cache (key TEXT PRIMARY KEY, saved REAL, value TEXT);
            CREATE TABLE IF NOT EXISTS evaluations (id INTEGER PRIMARY KEY, symbol TEXT, at TEXT, value TEXT);
            CREATE TABLE IF NOT EXISTS deliveries (key TEXT PRIMARY KEY, at TEXT, status TEXT);
            CREATE TABLE IF NOT EXISTS outbox (key TEXT PRIMARY KEY, symbol TEXT, value TEXT);
        ''')

    def get(self, key, max_age):
        row = self.db.execute('SELECT saved,value FROM cache WHERE key=?', (key,)).fetchone()
        if row and datetime.now(timezone.utc).timestamp() - row[0] < max_age:
            return json.loads(row[1])
        return None

    def put(self, key, value):
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO cache VALUES (?,?,?)',
                            (key, datetime.now(timezone.utc).timestamp(), encode(value)))

    def prune_cache(self, days=35):
        # Evaluations and delivery history are permanent; only replaceable HTTP cache expires.
        with self.db:
            self.db.execute('DELETE FROM cache WHERE saved<?',
                            (datetime.now(timezone.utc).timestamp() - days * 86400,))

    def previous(self, symbol, as_of):
        row = self.db.execute('SELECT value FROM evaluations WHERE symbol=? AND at<=? ORDER BY id DESC LIMIT 1',
                              (symbol, str(as_of))).fetchone()
        return json.loads(row[0]) if row else None

    def record(self, symbol, result):
        with self.db:
            self.db.execute('INSERT INTO evaluations(symbol,at,value) VALUES (?,?,?)',
                            (symbol, result['as_of'], encode(result)))

    def delivered(self, key):
        return self.db.execute('SELECT status FROM deliveries WHERE key=?', (key,)).fetchone()

    def delivery(self, key, status):
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO deliveries VALUES (?,?,?)',
                            (key, datetime.now(timezone.utc).isoformat(), status))

    def queue(self, key, symbol, value):
        with self.db:
            self.db.execute('INSERT OR IGNORE INTO outbox VALUES (?,?,?)', (key, symbol, encode(value)))

    def pending(self, symbols):
        rows = self.db.execute('SELECT key,symbol,value FROM outbox ORDER BY rowid').fetchall()
        return [(key, json.loads(value)) for key, symbol, value in rows
                if symbol in symbols and not self.delivered(key)]

    def close(self):
        self.db.close()


def digest(value):
    return hashlib.sha256(encode(value).encode('utf-8')).hexdigest()
