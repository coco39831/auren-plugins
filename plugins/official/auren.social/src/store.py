"""社交插件自有資料庫；僅索引識別碼，內容及歷史版本使用本機 Vault 加密。"""
from contextlib import contextmanager
from hashlib import sha256
from pathlib import Path
import sqlite3
import base64
import zstandard
from auren_contracts.models import Denied,DomainError,canonical,uid,now


class Store:
    def __init__(self,host):
        self.host=host;self.vault=host.vault
        self.compressor=zstandard.ZstdCompressor(level=9);self.decompressor=zstandard.ZstdDecompressor()
        self.path=host.root/'plugins-v1'/'data'/'auren.official.social'/'social.sqlite3'
        self.path.parent.mkdir(parents=True,exist_ok=True)
        self.db=sqlite3.connect(self.path,check_same_thread=False,isolation_level=None)
        self.db.row_factory=sqlite3.Row
        self.db.execute('PRAGMA foreign_keys=ON');self.db.execute('PRAGMA busy_timeout=5000')
        # 既有整體快照持有主機鎖；DELETE journal 讓靜止時可直接保存一致的單檔。
        self.db.execute('PRAGMA journal_mode=DELETE')
        source=(Path(__file__).parents[1]/'migrations/001_social.sql').read_text(encoding='utf-8')
        digest=sha256(source.encode()).hexdigest()
        self.db.execute('CREATE TABLE IF NOT EXISTS social_schema(version INTEGER PRIMARY KEY,checksum TEXT NOT NULL)')
        rows=list(self.db.execute('SELECT * FROM social_schema'))
        if rows:
            if len(rows)!=1 or rows[0]['version']!=1 or rows[0]['checksum']!=digest:raise DomainError('社交資料結構版本不相容')
        else:
            with self.transaction():
                for statement in source.split(';'):
                    if statement.strip():self.db.execute(statement)
                self.db.execute('INSERT INTO social_schema VALUES(1,?)',(digest,))
        self.closed=False
    @contextmanager
    def transaction(self):
        with self.host.lock:
            self.db.execute('BEGIN IMMEDIATE')
            try:yield;self.db.execute('COMMIT')
            except BaseException:self.db.execute('ROLLBACK');raise
    def one(self,sql,args=()):
        row=self.db.execute(sql,args).fetchone()
        return dict(row) if row else None
    def all(self,sql,args=()):return [dict(row) for row in self.db.execute(sql,args)]
    def encode(self,value):return 'social:zstd:1:'+base64.b64encode(self.vault.seal(self.compressor.compress(canonical(value).encode()))).decode('ascii')
    def decode(self,value):
        import json
        if value.startswith('social:zstd:1:'):return json.loads(self.decompressor.decompress(self.vault.open(base64.b64decode(value[len('social:zstd:1:'):])),max_output_size=2*1024*1024))
        return json.loads(self.vault.untext(value))
    def history(self,kind,identifier,actor,value):
        self.db.execute('INSERT INTO history VALUES(?,?,?,?,?,?)',(uid(),kind,identifier,actor,self.encode(value),now()))
    def audit(self,actor,kind,identifier):
        self.host.audit.append(actor,'social.'+kind,{'id':identifier})
    def close(self):
        if not self.closed:self.db.close();self.closed=True


def text(value,limit=16000,empty=False):
    if not isinstance(value,str) or len(value)>limit or not empty and not value.strip():raise DomainError('社交內容為空白或超過允許長度')
    return value.strip()

def identifier(value):
    import re
    if not isinstance(value,str) or not re.fullmatch('[a-zA-Z0-9:_-]{1,100}',value):raise DomainError('社交識別碼無效')
    return value

def page(value):
    offset,limit=value.get('offset',0),value.get('limit',30)
    if type(offset) is not int or offset<0 or type(limit) is not int or not 1<=limit<=100:raise DomainError('社交分頁範圍無效')
    return offset,limit
