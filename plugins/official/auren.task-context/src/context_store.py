"""核心擁有可刪除的上下文塊；編解碼經注入的記憶 Service，原文不進永久記憶庫。"""
from hashlib import sha256
import json
import threading
import weakref
from auren_contracts.models import DomainError,Denied,canonical,now,uid
from auren_contracts.task_context import ContextBlockV1


class ContextQuota:
    def __init__(self,limit):
        self.limit=limit; self.lock=threading.RLock(); self.stores=weakref.WeakValueDictionary()

    def register(self,store):
        with self.lock:self.stores[id(store)]=store

    def unregister(self,store):
        with self.lock:self.stores.pop(id(store),None)

    def enforce(self):
        with self.lock:
            while sum(store.size() for store in self.stores.values())>self.limit():
                choices=[(row['last_access'],row['id'],store,row) for store in list(self.stores.values()) for row in store.entries()]
                if not choices: return
                _,_,store,row=min(choices,key=lambda value:value[:2])
                store.before_clear(row['task_id'],'capacity')
                store.delete(row['id'])
                store.audit.append('context','context.expired',{'task_id':row['task_id'],'context_id':row['id'],'reason':'capacity'})


class ContextStore:
    def __init__(self,core,codec,vault,audit,before_clear,quota=None):
        self.db=core.db; self.codec=codec; self.vault=vault; self.audit=audit; self.before_clear=before_clear
        self.quota=quota or ContextQuota(lambda:max(1,core.parameter('context_store_mib'))*1024*1024)
        self.quota.register(self)

    def entries(self,task_id=None):
        return self.db.all('SELECT * FROM task_context_entries'+(' WHERE task_id=?' if task_id else ''),(task_id,) if task_id else ())

    def size(self):
        # 配額包含加密內容與索引欄位；不把整個核心資料庫的稽核／工作列算成臨時區。
        blocks=self.db.one('SELECT COALESCE(SUM(LENGTH(payload)+LENGTH(digest)+LENGTH(codec)+16),0) AS n FROM task_context_blocks')['n']
        entries=self.db.one('SELECT COALESCE(SUM(LENGTH(CAST(id||task_id||step_id||kind||slot||manifest||digest||created_at||last_access AS BLOB))+8),0) AS n FROM task_context_entries')['n']
        return blocks+entries

    def put(self,task_id,step_id,kind,slot,value,cold=False,enforce=True):
        text=canonical(value); blocks=self.codec.encode_context(text,cold); at=now()
        with self.quota.lock,self.db.transaction() as conn:
            old=self.db.one('SELECT * FROM task_context_entries WHERE task_id=? AND slot=?',(task_id,slot))
            identifier=old['id'] if old else uid()
            if old: self._release(conn,old)
            manifest=[]
            for block in blocks:
                # 同一塊的 codec 固定於首次寫入；重複引用不改寫密文。
                existing=self.db.one('SELECT digest FROM task_context_blocks WHERE digest=?',(block.digest,))
                if existing: conn.execute('UPDATE task_context_blocks SET refs=refs+1 WHERE digest=?',(block.digest,))
                else: conn.execute('INSERT INTO task_context_blocks VALUES(?,?,?,?,1)',(block.digest,block.codec,block.raw_size,self.vault.seal(block.data,('auren.context.v1:'+block.digest).encode())))
                manifest.append(block.digest)
            conn.execute('INSERT INTO task_context_entries VALUES(?,?,?,?,?,?,?,?,?,?)',(identifier,task_id,step_id,kind,slot,canonical(manifest),sha256(text.encode()).hexdigest(),len(text.encode()),old['created_at'] if old else at,at))
        if enforce: self.quota.enforce()
        return identifier

    def _release(self,conn,row):
        for digest in json.loads(row['manifest']): conn.execute('UPDATE task_context_blocks SET refs=refs-1 WHERE digest=?',(digest,))
        conn.execute('DELETE FROM task_context_entries WHERE id=?',(row['id'],))
        conn.execute('DELETE FROM task_context_blocks WHERE refs<=0')

    def delete(self,identifier):
        with self.quota.lock,self.db.transaction() as conn:
            row=self.db.one('SELECT * FROM task_context_entries WHERE id=?',(identifier,))
            if row: self._release(conn,row)

    def clear(self,task_id):
        for row in self.entries(task_id): self.delete(row['id'])

    def row(self,task_id,slot): return self.db.one('SELECT * FROM task_context_entries WHERE task_id=? AND slot=?',(task_id,slot))

    def read(self,identifier,task_id):
        row=self.db.one('SELECT * FROM task_context_entries WHERE id=? AND task_id=?',(identifier,task_id))
        if not row: raise DomainError('任務上下文不存在或已轉為總結')
        blocks=[]
        for digest in json.loads(row['manifest']):
            block=self.db.one('SELECT * FROM task_context_blocks WHERE digest=?',(digest,))
            if not block: raise DomainError('任務上下文缺少壓縮塊')
            blocks.append(ContextBlockV1(digest,block['codec'],block['raw_size'],self.vault.open(block['payload'],('auren.context.v1:'+digest).encode())))
        text=self.codec.decode_context(tuple(blocks))
        if sha256(text.encode()).hexdigest()!=row['digest']: raise DomainError('任務上下文完整性不符')
        self.db.execute('UPDATE task_context_entries SET last_access=? WHERE id=?',(now(),identifier))
        return json.loads(text)

    def get(self,task_id,slot,default=None):
        row=self.row(task_id,slot)
        return self.read(row['id'],task_id) if row else default

    def reference(self,task_id,step_id,kind,value):
        identifier=self.put(task_id,step_id,kind,'ref:'+uid(),value)
        row=self.db.one('SELECT * FROM task_context_entries WHERE id=?',(identifier,))
        if not row: return {'unavailable':True,'reason':'context_capacity','saved_in_task_summary':True}
        return {'context_reference':identifier,'raw_bytes':row['raw_size'],'sha256':row['digest'],'paged':True}

    def page(self,identifier,task_id,allowed_steps,offset=0,limit=65536):
        if type(offset) is not int or offset<0 or type(limit) is not int or not 1<=limit<=65536: raise DomainError('上下文分頁範圍無效')
        row=self.db.one('SELECT * FROM task_context_entries WHERE id=? AND task_id=?',(identifier,task_id))
        if not row or allowed_steps is not None and row['step_id'] not in allowed_steps: raise Denied('上下文參照不屬於目前任務的可讀範圍')
        text=canonical(self.read(identifier,task_id)); page=text[offset:offset+limit]
        return {'context_reference':identifier,'text':page,'offset':offset,'next_offset':offset+len(page) if offset+len(page)<len(text) else None,'total_chars':len(text),'sha256':row['digest']}
