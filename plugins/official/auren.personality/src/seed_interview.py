"""建立前的預設模型訪談；收據綁定本地登入，不能由前端偽造人格數值。"""
from contextlib import contextmanager
import json
import threading
from auren_contracts.models import DomainError,Denied,NotReady,Source,canonical,uid,now
from auren_contracts.personality import SeedProfileV1,personality_traits
from auren_core.personality_prompts import SEED_PROMPT


class SeedInterview:
    def __init__(self,host,model):self.host=host;self.model=model;self.lock=threading.RLock()

    def load(self,identifier,actor):
        if not isinstance(identifier,str) or len(identifier)>100:raise DomainError('種子問答不存在')
        saved=self.host.core.get_setting('system.seed_interview.'+identifier)
        if not saved:raise DomainError('種子問答不存在')
        value=json.loads(self.host.vault.untext(saved))
        if actor.source!=Source.HUMAN or value['session_id']!=actor.session_id:raise Denied('種子問答不屬於目前登入')
        return value

    def save(self,value):self.host.core.set_setting('system.seed_interview.'+value['id'],self.host.vault.text(canonical(value)))

    def public(self,value):
        return {key:value[key] for key in ('id','revision','turns','ready','question','summary','created_at')}

    def ask(self,state):
        if not self.model.model_configured():raise NotReady('請先設定全域預設模型，再開始種子問答')
        messages=[{'role':'system','content':SEED_PROMPT+'\n'+self.host.language.instruction},
            {'role':'user','content':canonical({'turns':state['turns'],'previous_summary':state.get('summary','')})}]
        if len(canonical(messages).encode())>2*1024*1024:raise DomainError('問答內容已超過單次模型容量，請重新整理回答')
        try:value=json.loads(self.model.model().complete(messages))
        except (ValueError,TypeError) as exc:raise DomainError('種子問答模型回應格式無效，請重試') from exc
        if not isinstance(value,dict) or type(value.get('ready')) is not bool:raise DomainError('種子問答模型回應格式無效，請重試')
        question=value.get('question');summary=value.get('summary');refs=value.get('evidence_turn_ids')
        if not isinstance(question,str) or len(question)>8000 or not isinstance(summary,str) or len(summary)>16000 or not isinstance(refs,list) or any(not isinstance(ref,str) for ref in refs):raise DomainError('種子問答模型回應格式無效，請重試')
        allowed={row['id'] for row in state['turns'] if row['role']=='user'}
        if not set(refs)<=allowed:raise DomainError('種子問答引用了不存在的回答')
        if value['ready']:
            if not allowed or not refs or not summary.strip() or question.strip():raise DomainError('種子問答尚未形成完整的互動風格')
            traits=personality_traits(value.get('traits'))
        else:
            if not question.strip():raise DomainError('種子問答尚未提供下一個問題')
            traits=None
        outgoing=summary if value['ready'] else question
        state.update({'ready':value['ready'],'question':question,'summary':summary,'traits':traits,'evidence_turn_ids':refs})
        state['turns'].append({'id':uid(),'role':'assistant','content':outgoing,'created_at':now()})
        return state

    def start(self,actor):
        if actor.source!=Source.HUMAN:raise Denied('種子問答只接受人工操作')
        self.host.license.check_device()
        with self.lock:
            state={'id':uid(),'session_id':actor.session_id,'revision':1,'turns':[],'ready':False,'question':'','summary':'','created_at':now(),'used_by':None}
            self.ask(state);self.save(state)
            return self.public(state)

    def answer(self,identifier,answer,revision,request_id,actor):
        if not isinstance(answer,str) or not answer.strip() or len(answer)>32000:raise DomainError('訊息需為 1 到 32000 字元')
        if not isinstance(request_id,str) or not 1<=len(request_id)<=100:raise DomainError('問答請求識別碼無效')
        with self.lock:
            state=self.load(identifier,actor)
            if state.get('used_by'):raise Denied('這份種子問答已用於建立 Agent')
            if state.get('last_request_id')==request_id:
                if state.get('last_answer')!=answer:raise Denied('同一問答請求不能更換內容')
                return self.public(state)
            if type(revision) is not int or state['revision']!=revision:raise Denied('種子問答已更新，請重新載入')
            state['turns'].append({'id':uid(),'role':'user','content':answer.strip(),'created_at':now()})
            self.ask(state);state['revision']+=1;state['last_request_id']=request_id;state['last_answer']=answer;self.save(state)
            return self.public(state)

    @contextmanager
    def claim(self,value,actor):
        if value.get('mode')!='seed':yield None;return
        with self.lock:
            state=self.load(value.get('seed_interview_id'),actor)
            if state.get('used_by'):raise Denied('這份種子問答已用於建立 Agent')
            if type(value.get('seed_revision')) is not int or state['revision']!=value['seed_revision'] or not state['ready'] or value.get('seed_confirmed') is not True:raise Denied('請先完成種子問答並確認目前的起點')
            profile=SeedProfileV1(state['id'],state['summary'],personality_traits(state['traits']),tuple(state['evidence_turn_ids']),tuple(state['turns']))
            yield profile
            state.update({'used_by':'created','used_at':now(),'turns':[],'traits':None,'evidence_turn_ids':[],'summary':'','question':''})
            state.pop('last_answer',None);self.save(state)

    def cancel(self,identifier,actor):
        with self.lock:
            state=self.load(identifier,actor)
            if state.get('used_by'):raise Denied('這份種子問答已用於建立 Agent')
            self.host.core.set_setting('system.seed_interview.'+identifier,None)
        return {'done':True}
