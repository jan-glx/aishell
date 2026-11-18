"""Root-owned, per-subject durable runCommand transcript store.

No shell commands or output are sent to the systemd journal. Browser reads
are always performed by the privileged executor after session validation.
"""
from __future__ import annotations

import os
import json
from contextlib import contextmanager
import sqlite3
import time
from pathlib import Path

DISPLAY_LIMIT = 10_000
HISTORY_LIMIT = 8


class CommandHistory:
    def __init__(self, path: Path):
        self.path = Path(path)
        if not self.path.is_absolute():
            raise ValueError('Command log database must be absolute')
        if self.path.parent.stat().st_uid != 0 or (self.path.parent.stat().st_mode & 0o022):
            raise PermissionError('Command log directory must be root-owned and not writable by others')
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS commands (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    subject TEXT NOT NULL,
                    unix_user TEXT NOT NULL,
                    command TEXT NOT NULL,
                    started REAL NOT NULL,
                    ended REAL,
                    exit_code INTEGER,
                    timed_out INTEGER NOT NULL DEFAULT 0,
                    truncated INTEGER NOT NULL DEFAULT 0,
                    error TEXT
                );
                CREATE INDEX IF NOT EXISTS commands_user ON commands(subject, id DESC);
                CREATE TABLE IF NOT EXISTS chunks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    command_id INTEGER NOT NULL REFERENCES commands(id),
                    stream TEXT NOT NULL CHECK(stream IN ('stdout','stderr')),
                    content TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS chunks_cmd ON chunks(command_id,id);
            ''')
        os.chmod(self.path, 0o600)
        with self.connect() as db:
            db.execute("UPDATE commands SET ended=?, error='Executor restarted' WHERE ended IS NULL", (time.time(),))

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        try:
            db.execute('PRAGMA busy_timeout=10000')
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def start(self, user, command: str) -> int:
        with self.connect() as db:
            c = db.execute('INSERT INTO commands(subject,unix_user,command,started) VALUES(?,?,?,?)',
                           (user.subject, user.username, command, time.time()))
            return c.lastrowid

    def append(self, command_id: int, stream: str, content: str) -> None:
        if content:
            with self.connect() as db:
                db.execute('INSERT INTO chunks(command_id,stream,content) VALUES(?,?,?)',
                           (command_id, stream, content))

    def finish(self, command_id: int, exit_code: int | None, timed_out=False,
               truncated=False, error: str | None = None) -> None:
        with self.connect() as db:
            db.execute('''UPDATE commands SET ended=?,exit_code=?,timed_out=?,truncated=?,error=?
                          WHERE id=?''', (time.time(), exit_code, int(timed_out),
                                        int(truncated), error, command_id))

    def read(self, subject: str, before_id: int | None = None) -> dict:
        if before_id is not None and (type(before_id) is not int or not 0 < before_id < 2**63):
            raise ValueError("Invalid command history cursor")
        with self.connect() as db:
            db.row_factory = sqlite3.Row
            records = db.execute('''SELECT * FROM commands WHERE subject=? AND (? IS NULL OR id < ?)
                                    ORDER BY id DESC LIMIT ?''', (subject, before_id, before_id, HISTORY_LIMIT + 1)).fetchall()
            has_older = len(records) > HISTORY_LIMIT
            records = records[:HISTORY_LIMIT]
            result = []
            for row in reversed(records):
                item = dict(row)
                chunks = db.execute('''SELECT stream,content FROM chunks WHERE command_id=? ORDER BY id''',
                                    (row['id'],)).fetchall()
                # Keep the newest DISPLAY_LIMIT Unicode characters in chronological order.
                remaining = DISPLAY_LIMIT
                clipped = []
                for chunk in reversed(chunks):
                    if remaining <= 0:
                        break
                    tail = chunk['content'][-remaining:]
                    clipped.append({'stream': chunk['stream'], 'content': tail})
                    remaining -= len(tail)
                item['chunks'] = list(reversed(clipped))
                item['display_clipped'] = any(len(c['content']) for c in chunks) and (
                    sum(len(c['content']) for c in chunks) > DISPLAY_LIMIT)
                # Each record must fit independently within the bridge response budget.
                # Keep the full command; clip only displayed stdout/stderr.
                while len(json.dumps(item, ensure_ascii=False).encode('utf-8')) > 17_000 and item['chunks']:
                    content = item['chunks'][0]['content']
                    if len(item['chunks']) > 1:
                        item['chunks'].pop(0)
                    elif len(content) > 1:
                        item['chunks'][0]['content'] = content[len(content)//2:]
                    else:
                        item['chunks'].clear()
                    item['display_clipped'] = True
                result.append(item)
            return {'commands': result, 'display_limit': DISPLAY_LIMIT,
                    'next_before_id': result[0]['id'] if has_older and result else None}
