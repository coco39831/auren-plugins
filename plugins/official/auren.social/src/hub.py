"""單一本機社交協調器；按需接話、可停止漫談及重新檢查授權後發布。"""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import json
import threading
from auren_contracts.models import Denied,DomainError,NotReady,canonical,uid,now
from auren_contracts.social import SocialActorV1,SOCIAL_CONTRACT
from auren_contracts.plugin_format import checked_schema,validate
from .store import Store,identifier


class Hub:
    def __init__(self,directory):
        self.directory=directory;self.host=directory.host;self.store=Store(self.host)
        self.pool=ThreadPoolExecutor(max_workers=4,thread_name_prefix='auren-social')
        self.references=0;self.closed=False;self.stops={};self.agent_locks={}
        self.schema=checked_schema(Path(__file__).parents[1]/'schemas/input.json')
        with self.store.transaction():
            self.store.db.execute("UPDATE activities SET status='interrupted',error='service_restarted',updated_at=? WHERE status IN ('queued','running')",(now(),))
            self.store.db.execute("UPDATE sessions SET status='interrupted',reason='service_restarted',updated_at=? WHERE status='running'",(now(),))
    def enabled(self,agent):
        try:
            return not self.closed and not agent.closed and not agent.shutting_down and not self.host.shutting_down and not self.host.closed and self.directory.resolve(agent.agent_id) is agent and agent.core.get_setting('onboarding_done',False) and agent.has_plugin(SOCIAL_CONTRACT)
        except DomainError:return False
    def agent(self,principal):
        if not isinstance(principal,str) or not principal.startswith('agent:'):raise DomainError('接話對象必須是本機 Agent')
        agent=self.directory.resolve(principal[6:])
        if not self.enabled(agent):raise NotReady('請先在接話對象安裝並啟用本地社交插件')
        return agent
    def queue(self,service,room_id,target,trigger_id,actor,session_id=None):
        with self.host.lock,self.store.transaction():
            room=service.room(room_id,actor)
            if not actor.human and actor.principal not in {row['principal'] for row in room['members']}:raise Denied('旁觀者不能要求其他 Agent 接話')
            if target not in {row['principal'] for row in room['members']}:raise Denied('接話對象不是聊天室成員')
            recipient=self.agent(target)
            if not recipient.model_configured() or not recipient.plugin_host.preparation()['ready']:raise NotReady('請先完成接話對象的模型連線與必要插件設定')
            if trigger_id:
                message,_=service.message(trigger_id,actor)
                if message['room_id']!=room_id:raise Denied('觸發訊息不屬於這個聊天室')
                previous=self.store.one("SELECT id,target,status FROM activities WHERE room_id=? AND target=? AND trigger_id=? AND status IN ('queued','running','done')",(room_id,target,trigger_id))
                if previous:return previous
            if self.store.one("SELECT COUNT(*) AS n FROM activities WHERE status IN ('queued','running')")['n']>=64:raise NotReady('目前等待接話的工作較多，請稍後再試')
            id_,at=uid(),now();brief={'goal':'依可見聊天室內容接話','trigger_message':trigger_id,'source_principal':actor.principal,
                'completion_conditions':['回覆非空白或明確略過','發言者身分正確','發布前重新通過聊天室與訊息可視權限檢查','訊息已實際保存'],
                'origin_agent':service.agent_id,'session_id':session_id}
            self.store.db.execute('INSERT INTO activities(id,room_id,target,trigger_id,requested_by,payload,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)',(id_,room_id,target,trigger_id,actor.principal,self.store.encode(brief),'queued',at,at))
            self.stops[id_]=threading.Event()
        if session_id is None:self.pool.submit(self.execute,id_)
        return {'id':id_,'target':target,'status':'queued'}
    def execute(self,id_):
        stop=self.stops.get(id_,threading.Event());agent_lock=None;acquired=False
        try:
            with self.host.lock:
                activity=self.store.one('SELECT * FROM activities WHERE id=?',(id_,))
                if not activity or activity['status']!='queued':return None
                if stop.is_set():
                    with self.store.transaction():self.store.db.execute("UPDATE activities SET status='cancelled',error='cancelled',updated_at=? WHERE id=?",(now(),id_))
                    return None
                brief=self.store.decode(activity['payload']);origin=self.directory.resolve(brief['origin_agent'])
                if not self.enabled(origin):raise Denied('origin_disabled')
                agent=self.agent(activity['target'])
                agent_lock=self.agent_locks.setdefault(agent.agent_id,threading.Lock())
            while not acquired:
                if stop.is_set():raise Denied('cancelled')
                acquired=agent_lock.acquire(timeout=.2)
            # 工廠建構及模型呼叫均在主機資料鎖之外，避免阻塞停止或其他聊天室。
            target=agent.plugin_host.provider(SOCIAL_CONTRACT)
            actor=SocialActorV1(activity['target'],agent.agent_id,False)
            with self.host.lock,self.store.transaction():
                if stop.is_set() or not self.enabled(origin) or not self.enabled(agent):raise Denied('cancelled')
                if brief.get('session_id'):
                    session=self.store.one('SELECT status FROM sessions WHERE id=?',(brief['session_id'],))
                    if not session or session['status']!='running':stop.set();raise Denied('cancelled')
                room=target.room(activity['room_id'],actor,True)
                if activity['trigger_id']:
                    trigger,_=target.message(activity['trigger_id'],actor)
                    if trigger['withdrawn']:raise Denied('trigger_withdrawn')
                rows=target.visible_messages(room,actor);selected=[];length=0
                for row in reversed(rows):
                    if row['withdrawn']:continue
                    if len(selected)>=30 or length+len(row['text'])>60000:break
                    selected.append({'id':row['id'],'revision':row['revision'],'author':row['author'],'name':target.name(row['author']),'text':row['text'],'has_image':bool(row.get('image'))});length+=len(row['text'])
                selected.reverse()
                packet={'room':{'id':room['id'],'title':room['title'],'kind':room['kind'],'audience':room['audience'],'owner':room['owner'],'revision':room['revision']},'you':actor.principal,'members':[{'principal':row['principal'],'name':target.name(row['principal'])} for row in room['members']],
                        'messages':selected,'context_window':{'loaded':len(selected),'has_earlier':len(selected)<len([row for row in rows if not row['withdrawn']])},'trigger_message':activity['trigger_id']}
                packet['your_profile']=target.public_profile(actor,actor.principal)
                packet['your_recent_posts']=target.read({'operation':'post.list','principal':actor.principal,'limit':5},actor)['posts']
                self.store.db.execute("UPDATE activities SET status='running',updated_at=? WHERE id=?",(now(),id_))
            if not agent.model_configured() or not agent.plugin_host.preparation()['ready']:raise NotReady('model_unconfigured')
            prompt=(Path(__file__).parents[1]/'prompts/reply.txt').read_text(encoding='utf-8')
            messages=[{'role':'system','content':agent.language.instruction+prompt}]
            if agent.has_plugin('auren.personality/1'):
                persona=agent.emotion.get_setting('persona',{})
                if persona:messages.append({'role':'system','content':'此 Agent 目前的人格資料：'+canonical(persona)})
            if agent.has_plugin('auren.emotion/1'):messages.append({'role':'system','content':'此刻的表達指引：'+canonical(agent.emotion.expression())})
            messages.append({'role':'user','content':canonical(packet)})
            with agent.plugin_host.snapshot(stopped=stop.is_set,timeout=180):raw=agent.model().complete(messages)
            result=json.loads(raw)
            if not isinstance(result,dict) or type(result.get('skip',False)) is not bool:raise DomainError('model_format')
            if not isinstance(result.get('text',''),str) or len(result.get('text',''))>16000:raise DomainError('model_format')
            if not result.get('skip',False) and not result.get('text','').strip():raise DomainError('model_empty')
            actions=result.get('actions',[])
            allowed_actions={'profile.update','post.create','post.update','post.withdraw','comment.add','message.update','message.withdraw','room.create','room.update','message.send','reaction.set'}
            if not isinstance(actions,list) or len(actions)>4 or result.get('skip',False) and actions:raise DomainError('model_actions')
            normalized=[]
            for index,action in enumerate(actions):
                if not isinstance(action,dict) or action.get('operation') not in allowed_actions:raise DomainError('model_actions')
                action=dict(action)
                if action['operation'] in ('post.create','message.send','comment.add'):action['request_id']='social-action-'+id_+'-'+str(index)
                validate(action,self.schema);normalized.append(action)
            with self.host.lock,self.store.transaction():
                current=self.store.one('SELECT status FROM activities WHERE id=?',(id_,))
                if stop.is_set() or current['status']!='running' or not self.enabled(origin) or not self.enabled(agent):raise Denied('cancelled')
                # 不發布給模型送出後才加入的成員；撤銷權限或收回觸發訊息時也不發布。
                actual=target.room(activity['room_id'],actor,True)
                if actual['revision']!=room['revision']:raise Denied('room_changed')
                for original in selected:
                    current_message,_=target.message(original['id'],actor)
                    if current_message['withdrawn'] or current_message['revision']!=original['revision']:raise Denied('context_changed')
                if activity['trigger_id']:
                    trigger,_=target.message(activity['trigger_id'],actor)
                    if trigger['withdrawn']:raise Denied('trigger_withdrawn')
                for action in normalized:target.mutate(action,actor)
                if normalized:self.store.history('reply.actions',id_,actor.principal,{'actions':normalized})
                self.store.db.execute('UPDATE members SET read_seq=MAX(read_seq,?) WHERE room_id=? AND principal=?',(max((row['seq'] for row in rows if row['id'] in {item['id'] for item in selected}),default=0),activity['room_id'],actor.principal))
                if result.get('skip',False):
                    self.store.db.execute("UPDATE activities SET status='skipped',updated_at=? WHERE id=?",(now(),id_));return None
                original_members={row['principal'] for row in room['members']}
                requested=result.get('audience') or {'mode':'selected','principals':sorted(original_members)}
                if requested.get('mode')=='selected':requested={**requested,'principals':sorted(set(requested.get('principals',[]))&original_members)}
                # 模型即使要求公開，本次接話也以讀取時的成員集合為上限。
                if requested.get('mode')=='public':requested={'mode':'selected','principals':sorted(original_members)}
                reply=target.send({'operation':'message.send','room':actual['id'],'text':result['text'],'audience':requested,'request_id':'reply-'+id_,'auto_reply':False},actor)
                self.store.db.execute("UPDATE activities SET status='done',reply_id=?,updated_at=? WHERE id=?",(reply['id'],now(),id_))
                self.store.history('reply.accepted',reply['id'],actor.principal,{'activity_id':id_,'verified':True,'message_id':reply['id']})
            self.store.audit(actor.principal,'reply.accepted',reply['id'])
            return reply['id']
        except Exception as exc:
            # 不保存模型錯誤原文、金鑰、提示詞或對話到錯誤欄位。
            code='cancelled' if stop.is_set() else 'permission_changed' if isinstance(exc,Denied) else 'model_unavailable' if isinstance(exc,NotReady) else 'model_or_service_error'
            self.host.diagnostics.record('social.reply_failed',level='ERROR',exception=exc)
            with self.host.lock:
                if not self.store.closed:
                    with self.store.transaction():self.store.db.execute("UPDATE activities SET status=?,error=?,updated_at=? WHERE id=? AND status IN ('queued','running')",('cancelled' if stop.is_set() else 'failed',code,now(),id_))
            return None
        finally:
            if acquired:agent_lock.release()
            with self.host.lock:self.stops.pop(id_,None)
    def control(self,service,value,actor):
        op=value['operation'];room_id=value.get('room')
        with self.host.lock:
            if op=='reply.cancel':
                row=self.store.one('SELECT * FROM activities WHERE id=?',(identifier(value['id']),))
                if not row:raise Denied('找不到接話工作')
                service.room(row['room_id'],actor)
                if not actor.human and actor.principal not in (row['target'],row['requested_by']):raise Denied('不能停止他人的接話工作')
                self.stops.get(row['id'],threading.Event()).set()
                with self.store.transaction():self.store.db.execute("UPDATE activities SET status='cancelled',error='cancelled',updated_at=? WHERE id=? AND status IN ('queued','running')",(now(),row['id']))
                return {'stopped':True}
            room=service.room(room_id,actor)
            if op=='reply.request':return self.queue(service,room_id,value.get('target'),value.get('message_id'),actor)
            if not actor.human:raise Denied('漫談的開啟與停止由本地使用者操作')
            if op=='auto.stop':
                session=self.store.one("SELECT * FROM sessions WHERE room_id=? AND status='running'",(room_id,))
                if session:
                    self.stops.get(session['id'],threading.Event()).set()
                    with self.store.transaction():self.store.db.execute("UPDATE sessions SET status='stopped',reason='user_stopped',updated_at=? WHERE id=?",(now(),session['id']))
                    for row in self.store.all("SELECT id,payload FROM activities WHERE room_id=? AND status IN ('queued','running')",(room_id,)):
                        if self.store.decode(row['payload']).get('session_id')==session['id']:self.stops.get(row['id'],threading.Event()).set()
                return {'stopped':True}
            if op!='auto.start':raise DomainError('不支援的接話控制')
            if value.get('confirmed') is not True:raise Denied('請先確認漫談會持續呼叫參與者的模型')
            unlimited=value.get('unlimited',False);limit=value.get('turn_limit',20)
            if type(unlimited) is not bool or not unlimited and (type(limit) is not int or not 1<=limit<=2147483647):raise DomainError('請設定正整數回合數，或明確選擇無上限')
            members=[row['principal'] for row in room['members'] if row['principal'].startswith('agent:')]
            if len(members)<2:raise DomainError('群組漫談至少需要兩個 Agent 成員')
            for member in members:
                participant=self.agent(member)
                if not participant.model_configured() or not participant.plugin_host.preparation()['ready']:raise NotReady('請先完成群組成員的模型連線與必要插件設定')
            if self.store.one("SELECT id FROM sessions WHERE room_id=? AND status='running'",(room_id,)):raise Denied('這個聊天室已有進行中的漫談')
            id_,at=uid(),now();stop=threading.Event();self.stops[id_]=stop
            with self.store.transaction():self.store.db.execute('INSERT INTO sessions(id,room_id,controller,turn_limit,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?)',(id_,room_id,service.agent_id,None if unlimited else limit,'running',at,at))
        self.pool.submit(self.run_session,service,id_,actor,stop)
        return {'id':id_,'running':True,'turn_limit':None if unlimited else limit}
    def run_session(self,service,id_,actor,stop):
        reason='user_stopped';index=0;silent=0
        try:
            while not stop.is_set():
                with self.host.lock:
                    session=self.store.one('SELECT * FROM sessions WHERE id=?',(id_,))
                    if session['status']!='running':return
                    if session['turn_limit'] is not None and session['turns']>=session['turn_limit']:reason='turn_limit_reached';break
                    if not self.enabled(self.directory.resolve(session['controller'])):reason='plugin_disabled';break
                    room=service.room(session['room_id'],actor)
                    members=[row['principal'] for row in room['members'] if row['principal'].startswith('agent:')]
                    if len(members)<2:reason='insufficient_members';break
                    target=members[index%len(members)];index+=1
                job=self.queue(service,room['id'],target,None,actor,id_)
                reply=self.execute(job['id'])
                with self.host.lock,self.store.transaction():
                    activity=self.store.one('SELECT status FROM activities WHERE id=?',(job['id'],))
                    self.store.db.execute('UPDATE sessions SET turns=turns+1,updated_at=? WHERE id=?',(now(),id_))
                    if activity['status'] not in ('done','skipped'):reason='reply_failed';break
                silent=0 if reply else silent+1
                if silent>=len(members):reason='participants_skipped';break
                # 僅讓出執行緒及檢查停止，不對無上限模式偷設回合上限。
                stop.wait(.25)
        except Exception:reason='session_unavailable'
        finally:
            with self.host.lock:
                if not self.store.closed:
                    with self.store.transaction():self.store.db.execute("UPDATE sessions SET status='stopped',reason=?,updated_at=? WHERE id=? AND status='running'",(reason,now(),id_))
                self.stops.pop(id_,None)
    def release(self,agent_id):
        with self.host.lock:
            self.references-=1
            for row in self.store.all("SELECT id,payload FROM activities WHERE status IN ('queued','running')"):
                if self.store.decode(row['payload'])['origin_agent']==agent_id:
                    self.stops.get(row['id'],threading.Event()).set()
                    with self.store.transaction():self.store.db.execute("UPDATE activities SET status='cancelled',error='plugin_stopped',updated_at=? WHERE id=?",(now(),row['id']))
            for row in self.store.all("SELECT id FROM sessions WHERE controller=? AND status='running'",(agent_id,)):
                self.stops.get(row['id'],threading.Event()).set()
                with self.store.transaction():self.store.db.execute("UPDATE sessions SET status='stopped',reason='plugin_disabled',updated_at=? WHERE id=?",(now(),row['id']))
            close=self.references==0
            if close:
                self.closed=True
                for stop in self.stops.values():stop.set()
        if close:
            self.pool.shutdown(wait=True,cancel_futures=True)
            with self.host.lock:self.store.close()
