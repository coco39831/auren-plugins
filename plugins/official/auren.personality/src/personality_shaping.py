"""問／答切換、人物素材與可重試的塑形提交；不借用成長開關。"""
from hashlib import sha256
import threading
from auren_contracts.models import Source,Denied,DomainError,canonical
from auren_contracts.personality import PersonalityPortraitV1,personality_traits
from auren_contracts.personality_reasoning import PersonalitySourceV1,CURRENT_ANSWER,reasoning,reasoning_style,resolve_current_answer
from .shaping_prompts import SHAPING_QUESTION,SHAPING_ANSWER


class PersonalityShaping:
    def __init__(self,runtime):self.r=runtime;self.lock=threading.RLock()

    def status(self):
        r=self.r
        if r.emotion.identity().get('mode')!='portrait':return {'available':False}
        try:r.emotion.require_shaping()
        except DomainError:return {'available':False}
        session=r.emotion.shaping_session()
        return {'available':True,'mode':session['mode'],'revision':session['revision']}

    def require(self,actor=None):
        if actor is None or actor.source!=Source.HUMAN:raise Denied('問答塑形只接受使用者操作')
        r=self.r;r.require_activated();r.emotion.require_shaping(actor)
        if not r.core.get_setting('onboarding_done',False):raise Denied('請先完成教學引導')
        if r.shutting_down or r.scheduler.paused or r.security.get_setting('locked',False):raise Denied('Agent 服務已暫停，請先完成安全事件處理')
        if not r.components.enabled('core'):raise Denied('此 Agent 的核心調度已停用')
        r.license.require_feature('agent')

    def idle(self):
        if any(row['status'] in ('in_progress','blocked') for row in self.r.permission.tasks()):raise Denied('請先完成或停止目前回覆，再切換問答模式')

    def conversation(self,session,identifier=None,create=True):
        r=self.r;items=r.conversations()
        if identifier:
            item=next((row for row in items if row['id']==identifier),None)
            if not item or item['archived']:raise Denied('對話已封存或不存在，請先恢復對話')
            return item
        pending=session.get('pending')
        candidates=[pending['conversation_id'] if pending else None,session.get('initial_conversation_id')]
        for value in candidates:
            item=next((row for row in items if row['id']==value and not row['archived']),None)
            if item:return item
        if not create:return None
        item=r.create_conversation(r.language.text('一起塑造一個人'))
        if not session['initial_conversation_id']:session['initial_conversation_id']=item['id']
        return item

    def context(self,session):
        # 完整素材留在壓縮原始庫；當次只帶人物輪廓與近期已發生的問答，不截斷原文。
        recent=[]
        for ref in session['recent'][-10:]:
            record=self.r.memory.read(ref['record_id'])
            recent.append({'record_id':record['id'],'role':ref['role'],'text':record['text']})
        return {'portrait':self.r.emotion.get_setting('persona',{}).get('portrait',{}),'recent':recent}

    def question(self,session):
        value=self.r.personality_dialogue.ask(SHAPING_QUESTION,self.context(session))
        return self.r.personality_dialogue.checked(value.get('question'),'shaping_question')

    def append_question(self,session,dialogue,conversation_id,question,key=None):
        ref=self.r.personality_dialogue.add_message(dialogue,conversation_id,'assistant',question,'persona_shaping_question',idempotency_key=key)
        session['pending']={**ref,'text':question};session['recent']=(session['recent']+[ref])[-10:]
        return ref

    def result(self,session,conversation_id):
        return {'persona_dialogue':True,'conversation_id':conversation_id,'shaping':{'available':True,'mode':session['mode'],'revision':session['revision']}}

    def open(self,actor):
        r=self.r
        with r.lock,self.lock:
            self.require(actor);self.idle()
            session=r.emotion.shaping_session();original_initial=session.get('initial_conversation_id');item=self.conversation(session,create=False)
            pending=session.get('pending')
            if item and session['mode']=='ask' and pending and pending['conversation_id']==item['id']:return self.result(session,item['id'])
            if item and session['mode']=='answer' and original_initial==item['id']:return self.result(session,item['id'])
            question=self.question(session) if session['mode']=='ask' else None
            self.require(actor)
            item=item or self.conversation(session)
            dialogue=r.emotion.personality_dialogue()
            if question:self.append_question(session,dialogue,item['id'],question)
            session['revision']+=1
            r.emotion.commit_shaping(session,dialogue,actor)
            return self.result(session,item['id'])

    def switch(self,mode,revision,conversation_id,actor):
        if mode not in ('ask','answer'):raise DomainError('請選擇問模式或答模式')
        r=self.r
        with r.lock,self.lock:
            self.require(actor);self.idle()
            session=r.emotion.shaping_session()
            if type(revision) is not int or revision!=session['revision']:raise Denied('問答模式已更新，請重新載入')
            item=self.conversation(session,conversation_id,create=False)
            if mode==session['mode']:
                if item and (mode=='answer' or session.get('pending') and session['pending']['conversation_id']==item['id']):return self.result(session,item['id'])
            dialogue=r.emotion.personality_dialogue();session['mode']=mode;session['pending']=None;session['last_request']=None
            question=self.question(session) if mode=='ask' else None
            self.require(actor)
            item=item or self.conversation(session,conversation_id)
            if question:self.append_question(session,dialogue,item['id'],question)
            session['revision']+=1;r.emotion.commit_shaping(session,dialogue,actor)
            return self.result(session,item['id'])

    def candidate(self,text,session,request_id,conversation_id):
        r=self.r;digest=sha256(text.encode()).hexdigest();saved=r.emotion.shaping_prepared()
        if saved and saved['request_id']==request_id:
            if saved['text_hash']!=digest or saved['revision']!=session['revision'] or saved['conversation_id']!=conversation_id:raise Denied('同一問答請求不能更換內容')
            return saved['value']
        pending=session.get('pending')
        data={**self.context(session),'message':text,'pending_question':pending['text'] if pending and pending['conversation_id']==conversation_id else None}
        value=r.personality_dialogue.ask(SHAPING_ANSWER,data)
        if type(value.get('updated')) is not bool:raise DomainError('人物輪廓格式無效，請重試')
        value['question']=r.personality_dialogue.checked(value.get('question'),'shaping_followup')
        if value['updated']:
            summary=value.get('summary');examples=value.get('speech_examples');quote=value.get('quote')
            if not isinstance(summary,str) or not summary.strip() or not isinstance(examples,list) or len(examples)>20 or any(not isinstance(item,str) or not 1<=len(item.strip())<=2000 for item in examples):raise DomainError('人物輪廓格式無效，請重試')
            if not isinstance(quote,str) or not quote.strip() or quote not in text:raise DomainError('人物塑形必須對應實際提供的資訊')
            value['traits']=personality_traits(value.get('traits'),partial=True)
            previous=r.emotion.get_setting('persona',{}).get('portrait',{})
            def source(identifier):
                if identifier==CURRENT_ANSWER:return PersonalitySourceV1(identifier,text,'user')
                return r.personality_reasoning.source(identifier)
            value['reasoning']=reasoning(value.get('reasoning'),previous.get('reasoning'),source)
            value['reasoning_style']=reasoning_style(value.get('reasoning_style'),previous.get('reasoning_style'))
            r.personality_dialogue.checked(summary,'shaping_summary')
            for example in examples:r.personality_dialogue.checked(example,'shaping_example')
        return value

    def submit(self,conversation_id,text,revision,request_id,actor):
        r=self.r
        if not isinstance(text,str) or not text.strip() or len(text)>32000:raise DomainError('訊息需為 1 到 32000 字元')
        if not isinstance(request_id,str) or not 1<=len(request_id)<=100:raise DomainError('問答請求識別碼無效')
        with r.lock,self.lock:
            self.require(actor)
            session=r.emotion.shaping_session();digest=sha256(text.encode()).hexdigest();last=session.get('last_request')
            if last and last['id']==request_id:
                if last['text_hash']!=digest or last['conversation_id']!=conversation_id:raise Denied('同一問答請求不能更換內容')
                return self.result(session,conversation_id)
            if session['mode']!='ask':raise Denied('只有問模式能以回答塑造人格')
            if type(revision) is not int or revision!=session['revision']:raise Denied('問答模式已更新，請重新載入')
            self.idle();self.conversation(session,conversation_id)
            value=self.candidate(text,session,request_id,conversation_id)
            # 先保存候選，重試沿用同一輸出；記憶寫入與人物版本不因 HTTP 重送而重複。
            r.emotion.prepare_shaping({'request_id':request_id,'text_hash':digest,'conversation_id':conversation_id,'revision':session['revision'],'value':value},actor)
            dialogue=r.emotion.personality_dialogue()
            ref=r.personality_dialogue.add_message(dialogue,conversation_id,'user',text,'persona_shaping_answer',idempotency_key='shaping.user:'+conversation_id+':'+request_id)
            session['recent']=(session['recent']+[ref])[-10:]
            self.append_question(session,dialogue,conversation_id,value['question'],'shaping.question:'+conversation_id+':'+request_id)
            portrait=PersonalityPortraitV1(value['summary'],tuple(value['speech_examples']),value['traits'],text,ref['record_id'],value['quote'],session['revision'],
                reasoning=resolve_current_answer(value.get('reasoning'),ref['record_id']),reasoning_style=value.get('reasoning_style')) if value['updated'] else None
            session['revision']+=1;session['last_request']={'id':request_id,'text_hash':digest,'conversation_id':conversation_id}
            r.emotion.commit_shaping(session,dialogue,actor,portrait)
            r.roaming.activity()
            return self.result(session,conversation_id)
