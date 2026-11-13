"""本機聊天室、群組與個人空間；每次讀寫都依宿主身分重新檢查可視權限。"""
import base64
import binascii
from hashlib import sha256
import json
import re
from auren_contracts.models import Denied,DomainError,NotReady,canonical,uid,now
from auren_contracts.social import SocialActorV1,SOCIAL_CONTRACT
from .store import text,identifier,page

USER='user:owner'
PLUGIN='auren.official.social'


class Social:
    def __init__(self,context,hub):
        self.context=context;self.hub=hub;self.store=hub.store
        self.runtime=context.require('auren.kernel.application/1')
        self.agent_id=self.runtime.agent_id;self.closed=False
        self.hub.references+=1
    def close(self):
        if not self.closed:
            self.closed=True;self.hub.release(self.agent_id)
    def actor(self,context):
        if context.get('source')=='human' and context.get('subject')=='owner':return SocialActorV1(USER,self.agent_id,True)
        if context.get('source')=='agent' and context.get('agent_id')==self.agent_id:
            self.hub.agent('agent:'+self.agent_id)
            return SocialActorV1('agent:'+self.agent_id,self.agent_id,False)
        raise Denied('社交操作缺少宿主驗證的身分')
    def people(self):
        people=[{'principal':USER,'name':'我','kind':'user','available':True}]
        for row in self.hub.directory.list()['agents']:
            agent=self.hub.directory.resolve(row['id'],allow_archived=True)
            people.append({'principal':'agent:'+row['id'],'name':row['name'],'kind':'agent','available':not row['archived'] and agent.has_plugin(SOCIAL_CONTRACT),'archived':row['archived'],'model_ready':agent.model_configured()})
        return people
    def principal(self,value):
        if value not in {row['principal'] for row in self.people()}:raise DomainError('找不到這位本機成員')
        return value
    def name(self,principal):
        return next((row['name'] for row in self.people() if row['principal']==principal),'已離開的 Agent')
    def audience(self,value,owner,default='private'):
        value={'mode':default,'principals':[]} if value is None else value
        if not isinstance(value,dict) or set(value)-{'mode','principals'} or value.get('mode') not in ('public','private','selected'):raise DomainError('請選擇有效的可視權限')
        recipients=value.get('principals',[])
        if not isinstance(recipients,list) or len(recipients)>200 or any(not isinstance(item,str) for item in recipients):raise DomainError('可視名單無效')
        for principal in recipients:self.principal(principal)
        return {'mode':value['mode'],'principals':sorted(set(recipients)|{owner}) if value['mode']=='selected' else []}
    @staticmethod
    def can(actor,owner,audience):
        return actor.human or actor.principal==owner or audience['mode']=='public' or audience['mode']=='selected' and actor.principal in audience['principals']
    def profile(self,principal):
        row=self.store.one('SELECT * FROM profiles WHERE principal=?',(principal,))
        if row:return {**row,**self.store.decode(row['payload'])}
        return {'principal':principal,'display_name':self.name(principal),'bio':'','audience':{'mode':'public','principals':[]},'revision':0}
    def public_profile(self,actor,principal):
        self.principal(principal);profile=self.profile(principal)
        if not self.can(actor,principal,profile['audience']):raise Denied('沒有這個個人空間的可視權限')
        return {key:profile[key] for key in ('principal','display_name','bio','audience','revision')}
    def update_profile(self,value,actor):
        target=value.get('principal',actor.principal);self.principal(target)
        if not actor.human and target!=actor.principal:raise Denied('Agent 只能設定自己的個人空間')
        old=self.profile(target)
        self.revision(value,old)
        payload={'display_name':text(value.get('display_name'),60),'bio':text(value.get('bio',''),2000,True),'audience':self.audience(value.get('audience'),target,'public')}
        self.store.history('profile',target,actor.principal,old)
        self.store.db.execute('INSERT INTO profiles VALUES(?,?,?,?) ON CONFLICT(principal) DO UPDATE SET payload=excluded.payload,revision=excluded.revision,updated_at=excluded.updated_at',(target,self.store.encode(payload),old['revision']+1,now()))
        return self.public_profile(actor,target)
    @staticmethod
    def revision(value,row):
        if type(value.get('revision')) is not int or value['revision']!=row['revision']:raise Denied('內容已變更，請重新整理後再修改')
    def room(self,value,actor,write=False):
        row=self.store.one('SELECT * FROM rooms WHERE id=?',(identifier(value),))
        if not row:raise Denied('聊天室不存在或沒有可視權限')
        row={**row,**self.store.decode(row['payload'])}
        row['members']=self.store.all('SELECT principal,read_seq,archived FROM members WHERE room_id=? ORDER BY principal',(row['id'],))
        allowed=self.can(actor,row['owner'],row['audience']) or any(item['principal']==actor.principal for item in row['members'])
        if not allowed:raise Denied('聊天室不存在或沒有可視權限')
        if write and actor.principal not in {item['principal'] for item in row['members']}:raise Denied('只有聊天室成員可以發言')
        return row
    def room_info(self,room,actor):
        visible=self.visible_messages(room,actor)
        state=self.store.one('SELECT * FROM room_states WHERE room_id=? AND principal=?',(room['id'],actor.principal)) or next((row for row in room['members'] if row['principal']==actor.principal),{})
        return {'id':room['id'],'kind':room['kind'],'owner':room['owner'],'title':room['title'],'audience':room['audience'],'revision':room['revision'],
                'members':[{**row,'name':self.name(row['principal'])} for row in room['members']],
                'can_post':actor.principal in {row['principal'] for row in room['members']},'can_manage':actor.human or actor.principal==room['owner'],
                'archived':bool(state.get('archived',False)),'unread':sum(row['seq']>state.get('read_seq',0) and row['author']!=actor.principal for row in visible),
                'last_message':self.message_info(visible[-1],actor) if visible else None,
                'session':self.store.one('SELECT id,status,turn_limit,turns,reason FROM sessions WHERE room_id=? ORDER BY created_at DESC,id DESC LIMIT 1',(room['id'],))}
    def create_room(self,value,actor):
        kind=value.get('kind','direct');members=value.get('members',[])
        if kind not in ('direct','group') or not isinstance(members,list) or any(not isinstance(item,str) for item in members):raise DomainError('聊天室成員或類型無效')
        members=sorted(set(members)|{actor.principal})
        if len(members)>200 or len(members)<2 or kind=='direct' and len(members)!=2:raise DomainError('私聊需要兩位成員，群組至少需要兩位成員')
        for member in members:self.principal(member)
        title=text(value.get('title') or ('與'+self.name(next(member for member in members if member!=actor.principal))+'聊天' if kind=='direct' else '新的群組'),80)
        audience=self.audience(value.get('audience') or {'mode':'selected','principals':members},actor.principal)
        identifier_=uid();at=now()
        self.store.db.execute('INSERT INTO rooms VALUES(?,?,?,?,1,?,?)',(identifier_,kind,actor.principal,self.store.encode({'title':title,'audience':audience}),at,at))
        for member in members:self.store.db.execute('INSERT INTO members(room_id,principal) VALUES(?,?)',(identifier_,member))
        return self.room_info(self.room(identifier_,actor),actor)
    def update_room(self,value,actor):
        room=self.room(value['id'],actor)
        if not actor.human and actor.principal!=room['owner']:raise Denied('只有建立者或本地使用者可以管理聊天室')
        self.revision(value,room)
        members=value.get('members',[row['principal'] for row in room['members']])
        if not isinstance(members,list) or any(not isinstance(item,str) for item in members) or len(set(members))<2 or len(set(members))>200 or room['owner'] not in members:raise DomainError('成員名單必須保留建立者及至少兩位成員')
        if room['kind']=='direct' and set(members)!={row['principal'] for row in room['members']}:raise Denied('私聊不能直接更換成員，請另建聊天室')
        for member in members:self.principal(member)
        self.store.history('room',room['id'],actor.principal,{key:room[key] for key in ('title','audience','members','revision')})
        previous={row['principal'] for row in room['members']}
        for member in previous-set(members):self.store.db.execute('DELETE FROM members WHERE room_id=? AND principal=?',(room['id'],member))
        for member in set(members)-previous:self.store.db.execute('INSERT INTO members(room_id,principal) VALUES(?,?)',(room['id'],member))
        payload={'title':text(value.get('title',room['title']),80),'audience':self.audience(value.get('audience',room['audience']),room['owner'])}
        self.store.db.execute('UPDATE rooms SET payload=?,revision=revision+1,updated_at=? WHERE id=?',(self.store.encode(payload),now(),room['id']))
        return self.room_info(self.room(room['id'],actor),actor)
    def visible_messages(self,room,actor):
        rows=[]
        for row in self.store.all('SELECT * FROM messages WHERE room_id=? ORDER BY seq',(room['id'],)):
            row={**row,**self.store.decode(row['payload'])}
            if self.can(actor,row['author'],row['audience']):rows.append(row)
        return rows
    def message(self,identifier_,actor):
        row=self.store.one('SELECT * FROM messages WHERE id=?',(identifier(identifier_),))
        if not row:raise Denied('訊息不存在或沒有可視權限')
        room=self.room(row['room_id'],actor);row={**row,**self.store.decode(row['payload'])}
        if not self.can(actor,row['author'],row['audience']):raise Denied('訊息不存在或沒有可視權限')
        return row,room
    def reactions(self,kind,identifier_,actor):
        rows=self.store.all('SELECT principal FROM reactions WHERE kind=? AND entity_id=?',(kind,identifier_))
        return {'count':len(rows),'liked':any(row['principal']==actor.principal for row in rows)}
    def message_info(self,row,actor):
        return {**{key:row[key] for key in ('id','room_id','seq','author','revision','withdrawn','created_at','updated_at','audience')},'author_name':self.name(row['author']),
                'text':'' if row['withdrawn'] else row['text'],'image':self.image_info(row.get('image')) if not row['withdrawn'] else None,
                'read_by':[member['principal'] for member in self.store.all('SELECT principal,read_seq FROM members WHERE room_id=?',(row['room_id'],)) if member['principal']!=row['author'] and member['read_seq']>=row['seq'] and self.can(SocialActorV1(member['principal'],member['principal'].removeprefix('agent:'),member['principal']==USER),row['author'],row['audience'])],
                'reactions':self.reactions('message',row['id'],actor),'can_edit':actor.principal==row['author'],'can_withdraw':actor.human or actor.principal==row['author']}
    @staticmethod
    def image_info(image):return {'mime':image['mime'],'bytes':image['bytes']} if image else None
    @staticmethod
    def image(value):
        if value is None:return None
        if not isinstance(value,dict) or set(value)!={'mime','data'} or not isinstance(value['data'],str) or len(value['data'])>700000:raise DomainError('圖片格式或大小無效')
        try:data=base64.b64decode(value['data'],validate=True)
        except (ValueError,binascii.Error):raise DomainError('圖片編碼無效') from None
        mime='image/png' if data.startswith(b'\x89PNG\r\n\x1a\n') else 'image/jpeg' if data.startswith(b'\xff\xd8\xff') else 'image/webp' if data[:4]==b'RIFF' and data[8:12]==b'WEBP' else None
        if not mime or mime!=value['mime'] or not 0<len(data)<=512*1024:raise DomainError('圖片只接受不超過 512 KiB 的 PNG、JPEG 或 WebP')
        return {'mime':mime,'data':value['data'],'bytes':len(data)}
    def message_audience(self,room,owner,requested=None):
        # 私密聊天室的訊息固定保留當時可見對象，日後放寬聊天室不會公開舊訊息。
        audience=self.audience(requested or {'mode':'selected','principals':[row['principal'] for row in room['members']]},owner)
        if room['audience']['mode']=='public':return audience
        permitted={room['owner'],*[row['principal'] for row in room['members']],*room['audience']['principals']}
        if audience['mode']=='private':return audience
        recipients=permitted if audience['mode']=='public' else permitted&set(audience['principals'])
        return {'mode':'selected','principals':sorted(recipients|{owner})}
    def send(self,value,actor):
        room=self.room(value['room'],actor,True)
        request_id=identifier(value.get('request_id'));digest=sha256(canonical(value).encode()).hexdigest()
        previous=self.store.one('SELECT * FROM messages WHERE author=? AND request_id=?',(actor.principal,request_id))
        if previous:
            if previous['request_hash']!=digest:raise Denied('同一送出識別碼不能改成不同訊息')
            row,_=self.message(previous['id'],actor);return self.message_info(row,actor)
        body=text(value.get('text',''),16000,True);image=self.image(value.get('image'))
        if not body and not image:raise DomainError('請輸入訊息或選擇圖片')
        mentions=value.get('mentions',[])
        if not isinstance(mentions,list) or any(member not in {row['principal'] for row in room['members']} for member in mentions):raise DomainError('提及對象必須是聊天室成員')
        payload={'text':body,'image':image,'mentions':mentions,'audience':self.message_audience(room,actor.principal,value.get('audience'))}
        seq=self.store.one('SELECT COALESCE(MAX(seq),0)+1 AS seq FROM messages WHERE room_id=?',(room['id'],))['seq'];at=now();id_=uid()
        self.store.db.execute('INSERT INTO messages(id,room_id,seq,author,payload,request_id,request_hash,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)',(id_,room['id'],seq,actor.principal,self.store.encode(payload),request_id,digest,at,at))
        self.store.db.execute('UPDATE rooms SET updated_at=? WHERE id=?',(at,room['id']))
        row,_=self.message(id_,actor);return self.message_info(row,actor)
    def post(self,id_,actor):
        row=self.store.one('SELECT * FROM posts WHERE id=?',(identifier(id_),))
        if not row:raise Denied('動態不存在或沒有可視權限')
        row={**row,**self.store.decode(row['payload'])}
        if not self.can(actor,row['author'],row['audience']) or not self.can(actor,row['author'],self.profile(row['author'])['audience']):raise Denied('動態不存在或沒有可視權限')
        return row
    def post_info(self,row,actor):
        comments=self.store.all('SELECT * FROM comments WHERE post_id=? ORDER BY created_at,id',(row['id'],))
        return {**{key:row[key] for key in ('id','author','revision','withdrawn','created_at','updated_at','audience')},'author_name':self.name(row['author']),
                'text':'' if row['withdrawn'] else row['text'],'image':self.image_info(row.get('image')) if not row['withdrawn'] else None,
                'reactions':self.reactions('post',row['id'],actor),'comments_count':len(comments),'can_edit':actor.principal==row['author'],'can_withdraw':actor.human or actor.principal==row['author']}
    def create_post(self,value,actor):
        request_id=identifier(value.get('request_id'));digest=sha256(canonical(value).encode()).hexdigest()
        previous=self.store.one('SELECT id,request_hash FROM posts WHERE author=? AND request_id=?',(actor.principal,request_id))
        if previous:
            if previous['request_hash']!=digest:raise Denied('同一發文識別碼不能改成不同動態')
            return self.post_info(self.post(previous['id'],actor),actor)
        body=text(value.get('text',''),16000,True);image=self.image(value.get('image'))
        if not body and not image:raise DomainError('請輸入動態內容或選擇圖片')
        payload={'text':body,'image':image,'audience':self.audience(value.get('audience'),actor.principal)}
        id_,at=uid(),now()
        self.store.db.execute('INSERT INTO posts(id,author,payload,request_id,request_hash,created_at,updated_at) VALUES(?,?,?,?,?,?,?)',(id_,actor.principal,self.store.encode(payload),request_id,digest,at,at))
        return self.post_info(self.post(id_,actor),actor)
    def update_content(self,value,actor,kind):
        row=self.message(value['id'],actor)[0] if kind=='message' else self.post(value['id'],actor)
        if row['author']!=actor.principal:raise Denied('只能編輯自己的發言或動態')
        if row['withdrawn']:raise Denied('已收回的內容不能重新編輯')
        self.revision(value,row);self.store.history(kind,row['id'],actor.principal,self.store.decode(row['payload']))
        payload=self.store.decode(row['payload']);payload['text']=text(value.get('text',payload['text']),16000,True)
        if 'audience' in value:
            payload['audience']=self.message_audience(self.room(row['room_id'],actor),row['author'],value['audience']) if kind=='message' else self.audience(value['audience'],row['author'])
        if not payload['text'] and not payload.get('image'):raise DomainError('內容不能為空白')
        table='messages' if kind=='message' else 'posts'
        self.store.db.execute('UPDATE '+table+' SET payload=?,revision=revision+1,updated_at=? WHERE id=?',(self.store.encode(payload),now(),row['id']))
        return self.message_info(self.message(row['id'],actor)[0],actor) if kind=='message' else self.post_info(self.post(row['id'],actor),actor)
    def comment(self,value,actor):
        post=self.post(value['post'],actor)
        if post['withdrawn']:raise Denied('這則動態已收回，不能留言')
        body=text(value.get('text'));request_id=identifier(value.get('request_id'));digest=sha256(canonical(value).encode()).hexdigest()
        previous=self.store.one('SELECT * FROM comments WHERE author=? AND request_id=?',(actor.principal,request_id))
        if previous:
            if previous['request_hash']!=digest:raise Denied('留言識別碼已用於其他內容')
            return {'id':previous['id']}
        id_=uid();self.store.db.execute('INSERT INTO comments(id,post_id,author,payload,request_id,request_hash,created_at) VALUES(?,?,?,?,?,?,?)',(id_,post['id'],actor.principal,self.store.encode({'text':body}),request_id,digest,now()))
        return {'id':id_}
    def activity_info(self,room,actor):
        self.room(room,actor)
        return self.store.all('SELECT id,target,status,reply_id,error,created_at,updated_at FROM activities WHERE room_id=? ORDER BY created_at DESC LIMIT 30',(room,))
    def read(self,value,actor):
        op=value['operation']
        if op=='bootstrap':return {'me':actor.principal,'human_can_view_all':True,'people':self.people(),'profile':self.public_profile(actor,actor.principal),'local_only':True}
        if op=='profile.get':return self.public_profile(actor,value.get('principal',actor.principal))
        if op=='room.list':
            result=[];offset,limit=page(value);query=text(value.get('query',''),200,True).casefold()
            for row in self.store.all('SELECT id FROM rooms ORDER BY updated_at DESC,id DESC'):
                try:room=self.room(row['id'],actor)
                except Denied:continue
                info=self.room_info(room,actor)
                if (not value.get('include_archived',False) and info['archived']) or query and query not in info['title'].casefold() and not any(query in message['text'].casefold() for message in self.visible_messages(room,actor)):continue
                result.append(info)
            return {'rooms':result[offset:offset+limit],'total':len(result),'offset':offset,'limit':limit}
        if op=='room.get':
            room=self.room(value['id'],actor);offset,limit=page(value)
            rows=self.visible_messages(room,actor);before=value.get('before')
            eligible=[row for row in rows if before is None or row['seq']<before]
            selected=eligible[-limit:]
            return {'room':self.room_info(room,actor),'messages':[self.message_info(row,actor) for row in selected],'has_earlier':len(eligible)>len(selected),'next_before':selected[0]['seq'] if selected else None,'activities':self.activity_info(room['id'],actor)}
        if op=='post.list':
            result=[];offset,limit=page(value);query=text(value.get('query',''),200,True).casefold();owner=value.get('principal')
            if owner:self.public_profile(actor,owner)
            for row in self.store.all('SELECT id FROM posts ORDER BY created_at DESC,id DESC'):
                try:post=self.post(row['id'],actor)
                except Denied:continue
                if post['withdrawn'] or owner and post['author']!=owner or query and query not in post['text'].casefold():continue
                result.append(self.post_info(post,actor))
            return {'posts':result[offset:offset+limit],'total':len(result),'offset':offset,'limit':limit}
        if op=='post.get':
            post=self.post(value['id'],actor);offset,limit=page(value)
            comments=self.store.all('SELECT * FROM comments WHERE post_id=? ORDER BY created_at,id',(post['id'],))
            return {'post':self.post_info(post,actor),'comments':[{'id':row['id'],'author':row['author'],'author_name':self.name(row['author']),'text':'' if row['withdrawn'] else self.store.decode(row['payload'])['text'],'withdrawn':row['withdrawn'],'created_at':row['created_at'],'can_withdraw':actor.human or actor.principal==row['author']} for row in comments[offset:offset+limit]],'total':len(comments)}
        if op=='asset.get':
            row=self.message(value['id'],actor)[0] if value.get('kind')=='message' else self.post(value['id'],actor)
            if row['withdrawn'] or not row.get('image'):raise Denied('找不到可讀取的圖片')
            return {'mime':row['image']['mime'],'data':row['image']['data']}
        if op=='activity.list':return {'activities':self.activity_info(value['room'],actor)}
        raise DomainError('不支援的社交讀取操作')
    def mutate(self,value,actor):
        op=value['operation']
        if op=='profile.update':return self.update_profile(value,actor)
        if op=='room.create':return self.create_room(value,actor)
        if op=='room.update':return self.update_room(value,actor)
        if op in ('room.archive','room.read'):
            room=self.room(value['id'],actor)
            self.store.db.execute('INSERT OR IGNORE INTO room_states(room_id,principal) VALUES(?,?)',(room['id'],actor.principal))
            if op=='room.archive':
                if type(value.get('archived')) is not bool:raise DomainError('請明確指定封存狀態')
                self.store.db.execute('UPDATE room_states SET archived=? WHERE room_id=? AND principal=?',(int(value['archived']),room['id'],actor.principal))
            else:
                visible=self.visible_messages(room,actor);maximum=max((row['seq'] for row in visible),default=0)
                self.store.db.execute('UPDATE room_states SET read_seq=MAX(read_seq,?) WHERE room_id=? AND principal=?',(maximum,room['id'],actor.principal))
                self.store.db.execute('UPDATE members SET read_seq=MAX(read_seq,?) WHERE room_id=? AND principal=?',(maximum,room['id'],actor.principal))
            return {'done':True}
        if op=='message.send':return self.send(value,actor)
        if op=='post.create':return self.create_post(value,actor)
        if op=='message.update':return self.update_content(value,actor,'message')
        if op=='post.update':return self.update_content(value,actor,'post')
        if op=='comment.add':return self.comment(value,actor)
        if op in ('message.withdraw','post.withdraw','comment.withdraw'):
            kind=op.split('.')[0];id_=identifier(value['id'])
            if kind=='message':row=self.message(id_,actor)[0]
            elif kind=='post':row=self.post(id_,actor)
            else:
                row=self.store.one('SELECT * FROM comments WHERE id=?',(id_,))
                if not row:raise Denied('找不到留言')
                self.post(row['post_id'],actor)
            if not actor.human and actor.principal!=row['author']:raise Denied('只能收回自己的內容')
            table={'message':'messages','post':'posts','comment':'comments'}[kind]
            self.store.db.execute('UPDATE '+table+' SET withdrawn=1 WHERE id=?',(id_,));return {'done':True}
        if op=='reaction.set':
            kind=value.get('kind');id_=identifier(value['id'])
            if kind=='message':self.message(id_,actor)
            elif kind=='post':self.post(id_,actor)
            else:raise DomainError('只支援訊息或動態的喜歡標記')
            if type(value.get('liked')) is not bool:raise DomainError('請明確指定喜歡狀態')
            if value['liked']:self.store.db.execute('INSERT OR IGNORE INTO reactions VALUES(?,?,?,?)',(kind,id_,actor.principal,now()))
            else:self.store.db.execute('DELETE FROM reactions WHERE kind=? AND entity_id=? AND principal=?',(kind,id_,actor.principal))
            return self.reactions(kind,id_,actor)
        raise DomainError('不支援的社交修改操作')
    def invoke(self,value,context):
        if self.closed:raise NotReady('社交插件已關閉')
        self.runtime.require_activated()
        if not self.runtime.has_plugin(SOCIAL_CONTRACT):raise Denied('這個 Agent 尚未啟用社交插件')
        actor=self.actor(context);op=value['operation']
        if op.startswith('auto.') or op.startswith('reply.'):
            return self.hub.control(self,value,actor)
        read=op in ('bootstrap','profile.get','room.list','room.get','post.list','post.get','asset.get','activity.list')
        with self.store.host.lock:
            if read:return self.read(value,actor)
            with self.store.transaction():result=self.mutate(value,actor)
            self.store.audit(actor.principal,op,result.get('id',value.get('id','local')))
        if op=='message.send' and actor.human:
            targets=list(value.get('mentions',[]))
            with self.store.host.lock:
                room=self.room(value['room'],actor)
                if value.get('auto_reply',True) and room['kind']=='direct' and not targets:targets=[row['principal'] for row in room['members'] if row['principal'].startswith('agent:')]
            requests=[]
            for target in dict.fromkeys(targets):
                try:requests.append(self.hub.queue(self,room['id'],target,result['id'],actor))
                except (Denied,NotReady,DomainError) as exc:requests.append({'target':target,'status':'blocked','error':str(exc)})
            result={**result,'reply_requests':requests}
        return result
