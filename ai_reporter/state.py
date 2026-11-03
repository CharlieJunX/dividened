import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


class State:
    def __init__(self, path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS ai_reports (
                report_id TEXT PRIMARY KEY, input_hash TEXT, instrument_id TEXT,
                created_at TEXT, value TEXT, artifact_dir TEXT);
            CREATE TABLE IF NOT EXISTS ai_calls (
                id INTEGER PRIMARY KEY, instrument_id TEXT, at TEXT, estimated_usd REAL,
                input_tokens INTEGER, output_tokens INTEGER, state TEXT);
            CREATE TABLE IF NOT EXISTS ai_deliveries (
                report_id TEXT PRIMARY KEY, at TEXT, state TEXT);
        ''')

    def close(self):
        self.db.close()

    def cached(self, hash_value):
        row = self.db.execute('SELECT value,artifact_dir FROM ai_reports WHERE input_hash=? ORDER BY created_at DESC LIMIT 1', (hash_value,)).fetchone()
        if not row or not (Path(row[1]) / 'report.html').exists():
            return None
        return json.loads(row[0])

    def report(self, report_id):
        row = self.db.execute('SELECT value FROM ai_reports WHERE report_id=?', (report_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def save(self, report, directory, input_hash):
        with self.db:
            # Old databases have a UNIQUE input_hash constraint. Preserve old reports
            # by retiring only their cache key when a cached result expires.
            self.db.execute('UPDATE ai_reports SET input_hash=NULL WHERE input_hash=?', (input_hash,))
            self.db.execute('INSERT INTO ai_reports VALUES (?,?,?,?,?,?)',
                            (report['report_id'], input_hash, report['instrument_id'], report['created_at'],
                             json.dumps(report, ensure_ascii=False, allow_nan=False), str(directory)))

    def reserve(self, instrument_id, config, estimate):
        day = datetime.now(timezone.utc).date().isoformat()
        count, spent = self.db.execute('SELECT COUNT(*),COALESCE(SUM(estimated_usd),0) FROM ai_calls WHERE at LIKE ?',
                                       (day + '%',)).fetchone()
        symbol_count = self.db.execute('SELECT COUNT(*) FROM ai_calls WHERE at LIKE ? AND instrument_id=?',
                                       (day + '%', instrument_id)).fetchone()[0]
        if (count >= config['max_calls_per_day'] or symbol_count >= config['max_calls_per_instrument_day'] or
                spent + estimate > config['max_estimated_usd_per_day']):
            return None
        with self.db:
            cur = self.db.execute('INSERT INTO ai_calls(instrument_id,at,estimated_usd,state) VALUES (?,?,?,?)',
                                  (instrument_id, datetime.now(timezone.utc).isoformat(), estimate, 'RESERVED'))
        return cur.lastrowid

    def finish_call(self, call_id, usage, status):
        with self.db:
            self.db.execute('UPDATE ai_calls SET input_tokens=?,output_tokens=?,state=? WHERE id=?',
                            (usage.get('input_tokens'), usage.get('output_tokens'), status, call_id))

    def delivery(self, report_id):
        row = self.db.execute('SELECT state FROM ai_deliveries WHERE report_id=?', (report_id,)).fetchone()
        return row[0] if row else None

    def mark_delivery(self, report_id, status):
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO ai_deliveries VALUES (?,?,?)',
                            (report_id, datetime.now(timezone.utc).isoformat(), status))
