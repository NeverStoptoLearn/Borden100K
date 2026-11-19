"""SQLite is both the durable queue and authority for immutable snapshots/events."""
import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from .core import Problem, dump, status, TERMINAL


class Store:
    def __init__(self, home):
        self.home = Path(home).resolve()
        self.home.mkdir(parents=True, exist_ok=True)
        self.path = self.home / 'backend.sqlite'
        with self.connect() as db:
            db.executescript('''
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS snapshots(kind TEXT,id TEXT,version INTEGER,body TEXT,deleted INTEGER DEFAULT 0,PRIMARY KEY(kind,id,version));
            CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,key TEXT UNIQUE,request TEXT,body TEXT,state TEXT,cancel INTEGER DEFAULT 0,heartbeat REAL,owner INTEGER,owner_birth REAL,processes TEXT DEFAULT '[]',context TEXT);
            CREATE TABLE IF NOT EXISTS events(sequence INTEGER PRIMARY KEY AUTOINCREMENT,run_id TEXT,body TEXT,event_type TEXT DEFAULT 'status_update');
            CREATE TABLE IF NOT EXISTS artifacts(run_id TEXT,id TEXT,body TEXT,PRIMARY KEY(run_id,id));
            CREATE TABLE IF NOT EXISTS stages(run_id TEXT,stage TEXT,state TEXT,body TEXT,PRIMARY KEY(run_id,stage));
            ''')
            if 'event_type' not in {r['name'] for r in db.execute('PRAGMA table_info(events)')}:
                db.execute("ALTER TABLE events ADD COLUMN event_type TEXT DEFAULT 'status_update'")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            db.execute('BEGIN IMMEDIATE')
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def put(self, kind, ident, version, body, deleted=False, *, initial_version=1):
        with self.connect() as db:
            latest = db.execute('SELECT max(version) FROM snapshots WHERE kind=? AND id=?', (kind, ident)).fetchone()[0] or 0
            if version != (latest + 1 if latest else initial_version):
                raise Problem('VERSION_CONFLICT', 'Version must be the next immutable version', 409)
            db.execute('INSERT INTO snapshots VALUES(?,?,?,?,?)', (kind, ident, version, dump(body), deleted))
        return body

    def get(self, kind, ident, version):
        with self.connect() as db:
            row = db.execute('SELECT * FROM snapshots WHERE kind=? AND id=? AND version=?', (kind, ident, version)).fetchone()
        if row is None or row['deleted']:
            raise Problem('INVALID_INPUT', 'Snapshot not found', 404)
        return json.loads(row['body'])

    def submit(self, request, context):
        key, ident, canonical = request['payload']['idempotency_key'], request['run_id'], dump(request)
        with self.connect() as db:
            row = db.execute('SELECT * FROM jobs WHERE key=?', (key,)).fetchone()
            if row:
                if row['request'] != canonical:
                    raise Problem('VERSION_CONFLICT', 'Idempotency key reused with different request', 409)
                return json.loads(row['body'])
            if db.execute('SELECT 1 FROM jobs WHERE id=?', (ident,)).fetchone():
                raise Problem('VERSION_CONFLICT', 'run_id already occupied', 409)
            body = status(request, 'queued')
            db.execute('INSERT INTO jobs(id,key,request,body,state,heartbeat,context) VALUES(?,?,?,?,?,?,?)',
                       (ident, key, canonical, dump(body), 'queued', time.time(), dump(context)))
            db.execute('INSERT INTO events(run_id,body) VALUES(?,?)', (ident, dump(body)))
            return body

    def job(self, ident):
        with self.connect() as db:
            row = db.execute('SELECT * FROM jobs WHERE id=?', (ident,)).fetchone()
        if row is None:
            raise Problem('INVALID_INPUT', 'Run not found', 404)
        return dict(row)

    def transition(self, ident, state, error=None, artifacts=None, progress=0):
        with self.connect() as db:
            return self._transition(db, ident, state, error, artifacts, progress)

    def _transition(self, db, ident, state, error=None, artifacts=None, progress=0):
        row = db.execute('SELECT * FROM jobs WHERE id=?', (ident,)).fetchone()
        old = row['state']
        if state not in {'queued': {'running', 'failed', 'cancelled'}, 'running': TERMINAL}.get(old, set()):
            raise Problem('VERSION_CONFLICT', f'Illegal transition {old}->{state}', 409)
        if state == 'succeeded' and row['cancel']:
            raise Problem('CANCELLED', 'Cancellation pending')
        body = status(json.loads(row['request']), state, old, error, artifacts, progress)
        db.execute('UPDATE jobs SET state=?,body=?,heartbeat=? WHERE id=?', (state, dump(body), time.time(), ident))
        for a in artifacts or []:
            db.execute('INSERT INTO artifacts VALUES(?,?,?)', (ident, a['artifact_id'], dump(a)))
        db.execute('INSERT INTO events(run_id,body) VALUES(?,?)', (ident, dump(body)))
        return body

    def recover_running(self, ident, should_recover, cleanup):
        """Serialize recheck, cleanup confirmation and the strict terminal transition.

        A second reaper must see the committed terminal state and skip. Holding
        the write transaction through bounded process cleanup avoids a lease
        state that could itself be abandoned after a recovery-worker crash.
        """
        with self.connect() as db:
            row = db.execute('SELECT * FROM jobs WHERE id=?', (ident,)).fetchone()
            if row is None or row['state'] != 'running' or not should_recover(row):
                return False
            cleanup(row)
            self._transition(db, ident, 'failed', dict(code='ENGINE_FAILED',
                message='Worker lost or heartbeat expired; processes cleaned', retryable=True))
            return True

    def claim(self, pid, birth):
        with self.connect() as db:
            row = db.execute("SELECT id FROM jobs WHERE state='queued' ORDER BY rowid LIMIT 1").fetchone()
            if not row:
                return None
            ident = row['id']
            # Reserve before releasing transaction; state transition is also atomic.
            row = db.execute('SELECT * FROM jobs WHERE id=?', (ident,)).fetchone()
            body = status(json.loads(row['request']), 'running', 'queued')
            db.execute("UPDATE jobs SET state='running',body=?,owner=?,owner_birth=?,heartbeat=? WHERE id=?", (dump(body), pid, birth, time.time(), ident))
            db.execute('INSERT INTO events(run_id,body) VALUES(?,?)', (ident, dump(body)))
        return ident

    def cancel(self, ident):
        self.job(ident)
        with self.connect() as db:
            current = db.execute('SELECT state,body FROM jobs WHERE id=?', (ident,)).fetchone()
            if current['state'] in TERMINAL:
                return json.loads(current['body'])
            db.execute("UPDATE jobs SET cancel=1 WHERE id=? AND state IN ('queued','running')", (ident,))
            row = db.execute('SELECT body FROM jobs WHERE id=?', (ident,)).fetchone()
            # Same authoritative state, new transport sequence acknowledges intent.
            db.execute("INSERT INTO events(run_id,body,event_type) VALUES(?,?,'cancel_requested')", (ident, row['body']))
        return json.loads(row['body'])

    def events(self, ident, after):
        with self.connect() as db:
            return [dict(sequence=r['sequence'], event_type=r['event_type'], envelope=json.loads(r['body'])) for r in db.execute(
                'SELECT * FROM events WHERE run_id=? AND sequence>? ORDER BY sequence', (ident, after))]
