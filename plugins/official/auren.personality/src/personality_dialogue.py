"""相遇時詢問期待；相關議題才排入回訪，不以計時器主動打斷對話。"""
import json
import threading
from auren_contracts.models import Source,Denied,DomainError,NotReady,canonical,uid,now
from auren_contracts.personality import PersonalityGoalV1,personality_traits
from .personality_prompts import WELCOME_PROMPT,EXPECTATION_PROMPT


class PersonalityDialogue:
    def __init__(self,runtime):self.r=runtime;self.lock=threading.RLock()

    def ask(self,prompt,data):
        if not self.r.model_configured():raise NotReady('請先設定推理模型，再開始這段問答')
        messages=[{'role':'system','content':prompt+'\n'+self.r.language.instruction},{'role':'user','content':canonical(data)}]
        if len(canonical(messages).encode())>2*1024*1024:raise DomainError('人格問答內容超過單次模型容量')
        try:value=json.loads(self.r.model().complete(messages))
        except (ValueError,TypeError) as exc:raise DomainError('人格問答模型回應格式無效，請重試') from exc
        if not isinstance(value,dict):raise DomainError('人格問答模型回應格式無效，請重試')
        return value

    def checked(self,text,kind):
        if not isinstance(text,str) or not text.strip() or len(text)>16000:raise DomainError('人格問答模型回應格式無效，請重試')
        return text.strip()

    def add_message(self,state,conversation_id,role,text,kind,idempotency_key=None):
        record=self.r.memory.append(text,'raw','event',{'role':role,'conversation_id':conversation_id,'kind':kind,'agent_id':self.r.agent_id},idempotency_key=idempotency_key)
        previous=next((ref for ref in state['messages'] if ref['record_id']==record),None)
        if previous:return previous
        ref={'record_id':record,'conversation_id':conversation_id,'role':role,'kind':kind,'created_at':now()}
        state['messages'].append(ref)
        return ref

    def messages(self,conversation_id):
        result=[]
        for ref in self.r.emotion.personality_dialogue()['messages']:
            if ref['conversation_id']!=conversation_id:continue
            record=self.r.memory.read(ref['record_id'])
            result.append({'id':record['id'],'role':ref['role'],'content':record['text'],'created_at':record['created_at']})
        return result

    def open(self):
        r=self.r
        # 與收訊、事件消費統一鎖定順序，避免初次相遇和一般對話互相等待。
        with r.lock,self.lock:
            identity=r.emotion.identity();state=r.emotion.personality_dialogue()
            if not identity.get('expectation_interview'):return {'required':False}
            if state['introduced']:
                return {'required':False,'conversation_id':state.get('initial_conversation_id')}
            r.require_activated()
            if not r.core.get_setting('onboarding_done',False):raise Denied('請先完成教學引導')
            if r.shutting_down or r.scheduler.paused or r.security.get_setting('locked',False):raise Denied('Agent 服務已暫停，請先完成安全事件處理')
            value=self.ask(WELCOME_PROMPT,{'name':identity['name'],'mode':identity['mode'],'initial_persona':identity.get('initial_persona',{}),
                'growth_enabled':r.emotion.get_setting('growth_enabled',True)})
            question=self.checked(value.get('question'),'first_meeting')
            conversation=r.create_conversation(r.language.text('第一次相遇'))
            ref=self.add_message(state,conversation['id'],'assistant',question,'persona_expectation_question')
            state.update({'introduced':True,'initial_conversation_id':conversation['id'],'pending_question':{**ref,'text':question},
                'last_question_at':now(),'last_question_turn':state['user_turns']})
            r.emotion.save_personality_dialogue(state)
            r.audit.append('persona','persona.expectation_asked',{'record_id':ref['record_id'],'reason':'first_meeting'})
            return {'required':True,'conversation_id':conversation['id']}

    def receive(self,conversation_id,text,actor):
        r=self.r
        if actor is None or actor.source!=Source.HUMAN:return None
        with self.lock:
            state=r.emotion.personality_dialogue()
            if not state['introduced']:return None
            pending=state.get('pending_question')
            if pending and pending['conversation_id']!=conversation_id:pending=None
            can_revisit=r.emotion.can_revisit_personality(state)
            value=self.ask(EXPECTATION_PROMPT,{'message':text,'pending_question':pending.get('text') if pending else None,
                'current_goal':r.emotion.personality_expectation()['goal'],'current_persona':r.emotion.get_setting('persona',{}),
                'growth_enabled':r.emotion.get_setting('growth_enabled',True),'can_revisit':can_revisit})
            if any(type(value.get(key)) is not bool for key in ('direct','defer','related')) or value['direct'] and value['defer']:raise DomainError('人格問答模型回應格式無效，請重試')
            quote=value.get('quote','');question=value.get('question','')
            if not isinstance(quote,str) or not isinstance(question,str) or len(question)>8000:raise DomainError('人格問答模型回應格式無效，請重試')
            relevant=bool(value['related'] and quote.strip() and quote in text)
            if value['direct'] and not relevant:raise DomainError('人格期待必須對應使用者實際說過的內容')
            if value['defer'] and not pending:raise DomainError('這則訊息沒有待回答的人格問題')
            reply=None;traits=None;summary=None
            if value['direct']:
                summary=value.get('summary')
                if not isinstance(summary,str) or not summary.strip() or len(summary)>16000:raise DomainError('人格期待缺少使用者回答')
                traits=personality_traits(value.get('target_traits'),partial=True);reply=self.checked(value.get('reply'),'expectation_reply')
                self.checked(summary,'expectation_summary')
            elif value['defer']:reply=self.checked(value.get('reply'),'expectation_deferred')
            state['user_turns']+=1;state['last_user_id']=uid()
            if value['direct'] or value['defer']:
                user=self.add_message(state,conversation_id,'user',text,'persona_expectation_answer')
                if value['direct']:
                    r.emotion.remember_personality_goal(PersonalityGoalV1(summary.strip(),traits,text,user['record_id']),actor)
                self.add_message(state,conversation_id,'assistant',reply,'persona_expectation_reply')
                state['pending_question']=None;state['last_question_at']=now();state['last_question_turn']=state['user_turns']
                state['deferred']={};r.emotion.save_personality_dialogue(state)
                return {'persona_dialogue':True,'conversation_id':conversation_id,'goal':r.emotion.personality_expectation()['goal']}
            # 只存當下話題產生的候選問題；一般任務照原流程處理，不能搶走驗收回答。
            if pending:state['pending_question']=None
            if can_revisit and relevant and question.strip():
                state['deferred'][conversation_id]={'source_text':text,'user_event_id':state['last_user_id'],'question':question.strip(),'created_at':now()}
            else:state['deferred'].pop(conversation_id,None)
            r.emotion.save_personality_dialogue(state)
            return None

    def bind_task(self,task_id,conversation_id,text):
        with self.lock:
            state=self.r.emotion.personality_dialogue();candidate=state['deferred'].get(conversation_id)
            if candidate and candidate['source_text']==text:
                candidate['task_id']=task_id;self.r.emotion.save_personality_dialogue(state)

    def after_task(self,task_id):
        r=self.r
        with r.lock,self.lock:
            state=r.emotion.personality_dialogue()
            match=next(((key,row) for key,row in state['deferred'].items() if row.get('task_id')==task_id),None)
            if not match:return
            conversation_id,candidate=match
            if r.permission.task(task_id)['status']!='done':return
            if not r.emotion.can_revisit_personality(state) or state['last_user_id']!=candidate['user_event_id']:
                state['deferred'].pop(conversation_id,None);r.emotion.save_personality_dialogue(state);return
            item=next((row for row in r.conversations() if row['id']==conversation_id),None)
            if not item or item['archived']:return
            question=self.checked(candidate['question'],'contextual_revisit')
            ref=self.add_message(state,conversation_id,'assistant',question,'persona_expectation_question',idempotency_key='persona.revisit:'+task_id)
            state.update({'pending_question':{**ref,'text':question},'last_question_at':now(),'last_question_turn':state['user_turns']})
            state['deferred'].pop(conversation_id,None);r.emotion.save_personality_dialogue(state)
            r.audit.append('persona','persona.expectation_asked',{'record_id':ref['record_id'],'reason':'related_topic','task_id':task_id})
