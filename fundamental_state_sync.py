"""Optional versioned S3 persistence. Requires AWS CLI and standard credentials."""
import argparse
import json
import os
from pathlib import Path
import sqlite3
import subprocess
from contextlib import closing


def aws(*args):
    result = subprocess.run(['aws', *args, '--output', 'json'], capture_output=True,
                            text=True, encoding='utf-8', errors='replace', check=False)
    if result.returncode:
        raise RuntimeError('S3 state operation failed; inspect bucket, credentials and permissions')
    return result.stdout


def sync(mode, path, bucket, key, initialize=False):
    if not bucket or not key:
        raise ValueError('Explicit bucket and object key required')
    versioning = json.loads(aws('s3api', 'get-bucket-versioning', '--bucket', bucket))
    if versioning.get('Status') != 'Enabled':
        raise ValueError('Durable state bucket must have versioning enabled')
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    staging = path.with_suffix('.transfer.sqlite3')
    if mode == 'restore':
        objects = json.loads(aws('s3api', 'list-objects-v2', '--bucket', bucket, '--prefix', key))
        exists = any(item['Key'] == key for item in objects.get('Contents', []))
        if not exists:
            if initialize:
                return
            raise ValueError('State object missing; explicit first-run initialization required')
        aws('s3api', 'get-object', '--bucket', bucket, '--key', key, str(staging))
        with closing(sqlite3.connect(staging)) as db:
            if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise ValueError('Corrupt persisted financial state')
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not {'cache', 'evaluations', 'deliveries', 'outbox'} <= tables:
                raise ValueError('Persisted financial state schema mismatch')
        staging.replace(path)
    else:
        if not path.is_file():
            raise ValueError('No local state to persist')
        with closing(sqlite3.connect(path)) as source, closing(sqlite3.connect(staging)) as target:
            source.backup(target)
        aws('s3api', 'put-object', '--bucket', bucket, '--key', key, '--body', str(staging),
            '--server-side-encryption', 'AES256')
        staging.unlink()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['restore', 'save'])
    parser.add_argument('--path', type=Path, default=Path('state/fundamentals.sqlite3'))
    args = parser.parse_args()
    sync(args.mode, args.path, os.environ.get('FUNDAMENTAL_STATE_BUCKET'),
         os.environ.get('FUNDAMENTAL_STATE_KEY') or 'dividend/fundamentals.sqlite3',
         initialize=os.environ.get('FUNDAMENTAL_STATE_INITIALIZE') == 'true')


if __name__ == '__main__':
    main()
