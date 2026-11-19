"""Private local persistent state. Never put this database in public artifacts."""
import json
import hashlib
from xml.etree import ElementTree as ET
import sqlite3
from pathlib import Path
import time


class State:
    def __init__(self,path):
        Path(path).parent.mkdir(parents=True,exist_ok=True)
        self.db=sqlite3.connect(path)
        self.db.row_factory=sqlite3.Row
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS sources (
          id TEXT PRIMARY KEY, body BLOB NOT NULL, sha TEXT NOT NULL,
          etag TEXT, modified TEXT);
        CREATE TABLE IF NOT EXISTS normalized (
          id TEXT PRIMARY KEY, tree BLOB NOT NULL, mapping TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS events (
          source TEXT NOT NULL, fingerprint TEXT NOT NULL, payload BLOB NOT NULL,
          PRIMARY KEY(source,fingerprint));
        CREATE TABLE IF NOT EXISTS artwork (
          key TEXT PRIMARY KEY, value TEXT NOT NULL, expires REAL NOT NULL);
        ''')

    def __enter__(self): return self

    def __exit__(self,*args): self.db.close()

    def source(self,key):
        return self.db.execute('SELECT sources.*, normalized.tree, normalized.mapping FROM sources LEFT JOIN normalized USING(id) WHERE sources.id=?',(key,)).fetchone()

    def save_source(self,key,body,sha,etag,modified,root=None,mapping="{}"):
        with self.db:
            previous=self.source(key)
            self.db.execute('INSERT OR REPLACE INTO sources VALUES (?,?,?,?,?)',
                            (key,body,sha,etag,modified))
            if root is not None and (not previous or previous['sha']!=sha or previous['mapping']!=mapping or previous['tree'] is None):
                self.db.execute('INSERT OR REPLACE INTO normalized VALUES (?,?,?)',
                                (key,ET.tostring(root),mapping))
                current=set()
                for programme in root.findall('programme'):
                    payload=ET.tostring(programme)
                    fingerprint=hashlib.sha256(payload).hexdigest()
                    current.add(fingerprint)
                    self.db.execute('INSERT OR IGNORE INTO events VALUES (?,?,?)',
                                    (key,fingerprint,payload))
                previous=self.db.execute('SELECT fingerprint FROM events WHERE source=?',(key,)).fetchall()
                self.db.executemany('DELETE FROM events WHERE source=? AND fingerprint=?',
                                   [(key,row[0]) for row in previous if row[0] not in current])

    def cached(self,key):
        row=self.db.execute('SELECT value,expires FROM artwork WHERE key=?',(key,)).fetchone()
        if row is not None and row['expires']>time.time():
            return True,json.loads(row['value'])
        return False,None

    def cache(self,key,value,ttl):
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO artwork VALUES (?,?,?)',
                            (key,json.dumps(value),time.time()+ttl))
