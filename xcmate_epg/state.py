"""Private local persistent state. Never put this database in public artifacts."""
import json
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
        CREATE TABLE IF NOT EXISTS artwork (
          key TEXT PRIMARY KEY, value TEXT NOT NULL, expires REAL NOT NULL);
        ''')

    def __enter__(self): return self

    def __exit__(self,*args): self.db.close()

    def source(self,key):
        return self.db.execute('SELECT * FROM sources WHERE id=?',(key,)).fetchone()

    def save_source(self,key,body,sha,etag,modified):
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO sources VALUES (?,?,?,?,?)',
                            (key,body,sha,etag,modified))

    def cached(self,key):
        row=self.db.execute('SELECT value,expires FROM artwork WHERE key=?',(key,)).fetchone()
        if row is not None and row['expires']>time.time():
            return True,json.loads(row['value'])
        return False,None

    def cache(self,key,value,ttl):
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO artwork VALUES (?,?,?)',
                            (key,json.dumps(value),time.time()+ttl))
