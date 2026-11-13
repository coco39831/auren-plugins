"""序列執行的企劃／細節上下文；交接是過程，完整文字只存核心臨時區。"""
from dataclasses import asdict
from datetime import datetime,timedelta,timezone
from hashlib import sha256
import json
import threading
from auren_contracts.models import DomainError,Denied,Event,canonical,now,uid
from .context_store import ContextStore
from .context_contracts import handoff,texts,string
from .task_context_prompts import PROCEDURE_PROMPT,HANDOFF_PROMPT,PARENT_DIAGNOSIS_PROMPT,DIAGNOSIS_PROMPT,DIAGNOSIS_REVIEW_PROMPT,CONTEXT_READ_PROMPT


class TaskContext:
    def __init__(self,runtime,quota=None):
        self.r=runtime; self.local=threading.local(); self.summarizing=set()
        self.store=ContextStore(runtime.core,runtime.memory,runtime.vault,runtime.audit,self.summarize,quota)
        runtime.events.subscribe('context.knowledge_conflict','task.knowledge_conflict',lambda event:runtime.learning.register_task_diagnostic(event.payload))
        runtime.events.subscribe('context.knowledge_diagnosed','learning.task_knowledge_diagnosed',self.persist_diagnosis)
        runtime.events.subscribe('context.task_done','task.done',lambda event:self.terminal(event.payload['task_id'],'done'))
        runtime.events.subscribe('context.task_stopped','task.stopped',lambda event:self.terminal(event.payload['task_id'],'suspended'))

    def key(self,identifier,step=None):
        state=self.r.task_workflow.state(identifier); step=step or (self.r.task_workflow.current(identifier) if state.get('plan') else None)
        name=step['id'] if step else 'planning'
        revision=state.get('method_revisions',{}).get(name,0)
        return f"session:{state['plan_revision']}:{name}:{revision}",name

    def attempt(self,identifier):
        slot,name=self.key(identifier)
        existing=self.store.get(identifier,slot,None)
        if existing is not None:return existing
        state=self.r.task_workflow.state(identifier); method=state.get('method_revisions',{}).get(name,0)
        saved=next((row for row in self.summary(identifier).get('active_procedures',[]) if row['step_id']==name and row.get('method_revision',0)==method and row.get('plan_revision')==state['plan_revision']),{})
        return {'attempt_id':uid(),'created_at':now(),'messages':[],'procedure':saved.get('procedure'),'used_ids':saved.get('used_ids',[]),'evidence_ids':saved.get('evidence_ids',[]),'step_id':name,'method_revision':method,'plan_revision':state['plan_revision']}

    def save_attempt(self,identifier,value):
        slot,name=self.key(identifier)
        self.store.put(identifier,name,'detail' if name!='planning' else 'planning',slot,value)

    def summary(self,identifier):
        state=self.r.task_workflow.state(identifier); pointer=self.r.core.get_setting('task_context.summary.'+identifier) or state.get('context_summary')
        if not pointer:return {}
        value=json.loads(self.r.memory.read(pointer['record_id'])['text'])
        if value.get('task_id')!=identifier:raise Denied('上下文總結不屬於目前任務')
        return value

    def handoffs(self,identifier):
        values=self.store.get(identifier,'handoffs',None)
        if values is None:values=self.summary(identifier).get('parent',{}).get('handoffs',{})
        revision=self.r.task_workflow.state(identifier)['plan_revision']
        return {key:value for key,value in values.items() if value.get('platform',{}).get('plan_revision')==revision}

    def parent(self,identifier):
        r=self.r; state=r.task_workflow.state(identifier); brief=state.get('brief') or {}
        saved=self.store.get(identifier,'parent',None)
        if saved is None:saved=self.summary(identifier).get('parent',{})
        return {'version':1,'task_id':identifier,'objective':r.permission.task(identifier)['goal'],
            'completion_checklist':[{**row,'passed':next((value['passed'] for value in state['checks'] if value['id']==row['id']),False)} for row in state['plan']['criteria']],
            'trigger':brief.get('trigger'),'subtasks':{'plan':state['plan']['steps'],'progress':state['step_status']},
            'accumulated_dead_ends':saved.get('accumulated_dead_ends',[]),'knowledge_conflicts':saved.get('knowledge_conflicts',[]),
            'handoffs':self.handoffs(identifier),'agent_notes':saved.get('agent_notes',''),'latest_acceptance':state['checks'],'user_feedback':state['feedback'],
            'restored_summary':self.r.core.get_setting('task_context.summary.'+identifier) or state.get('context_summary'),
            'working_memory':self.store.get(identifier,'parent_working_memory',None),
            'planning_context':[{'context_reference':row['id'],'slot':row['slot'],'raw_bytes':row['raw_size'],'sha256':row['digest'],'paged':True} for row in self.store.entries(identifier) if row['step_id']=='planning' and row['kind']=='stage']}

    def parent_state(self,identifier):
        saved=self.store.get(identifier,'parent',None)
        return saved if saved is not None else self.summary(identifier).get('parent',{})

    def packet(self,identifier,step,knowledge):
        r=self.r; state=r.task_workflow.state(identifier)
        if step is None: return {**self.parent(identifier),'knowledge':knowledge,'layer':'planning'}
        criteria=[row for row in state['plan']['criteria'] if row['id'] in step['criterion_ids']]
        objectives=[row['text'] for row in state['plan']['requirements'] if set(row['criterion_ids'])&set(step['criterion_ids'])]
        previous=self.handoffs(identifier); direct={key:previous[key] for key in step['depends_on'] if key in previous}
        context=self.store.get(identifier,'parent',None)
        if context is None:context=self.summary(identifier).get('parent',{})
        attempt=self.attempt(identifier)
        notes=[row for value in direct.values() for row in value.get('decisions',[])]
        unresolved=[row for value in direct.values() for row in value.get('unresolved',[])]
        dead=context.get('accumulated_dead_ends',[])
        result={'version':1,'layer':'detail','subtask_id':step['id'],'goal':{'title':step['title'],'objective':'\n'.join(objectives),'criteria':criteria},
            'method_from_plan':{'goal':step['goal']},'procedure':{**(attempt.get('procedure') or {'steps':[],'depends_on':step['depends_on']}),'known_dead_ends':dead},
            'tools':knowledge.get('tools',[]),'tool_dependency_gaps':knowledge.get('dependency_gaps',[]),
            'knowledge':{'required':knowledge['required'],'queries':knowledge['queries'],'context':knowledge['knowledge'],'missing':knowledge['missing'],'gaps':knowledge.get('gaps',[])},
            'handoff_from_previous':{'by_subtask':direct,'decisions':notes,'unresolved':unresolved,'dead_ends':[row for value in direct.values() for row in value.get('dead_ends',[])]},'agent_notes':attempt.get('agent_notes','')}
        if r.has_plugin('auren.sharing/1') and getattr(r,'shared_data',None): result['shared_information']=r.shared_data.context(r.agent_id,result['goal']['objective'])
        return result

    def prepare_procedure(self,identifier,packet,force=False):
        if packet['layer']!='detail': return
        self.activate(identifier)
        attempt=self.attempt(identifier)
        if attempt.get('procedure') and not force: return
        response=self.r.task_workflow.ask('procedure',PROCEDURE_PROMPT,{'subtask':packet,'previous_procedure':attempt.get('procedure'),'own_context':attempt['messages'] if force else []})
        if not isinstance(response,dict) or not texts(response.get('steps')) or any(not value.strip() for value in response['steps']): raise DomainError('子任務流程必須包含非空步驟')
        attempt['procedure']={'version':1,'steps':response['steps'],'depends_on':self.r.task_workflow.current(identifier)['depends_on'],
            'known_dead_ends':packet['procedure']['known_dead_ends'],'agent_notes':string(response.get('agent_notes',''),True)}
        attempt['agent_notes']=attempt['procedure']['agent_notes']; self.save_attempt(identifier,attempt)
        state=self.r.task_workflow.state(identifier)
        for row in state['plan']['steps']:
            if row['id']==packet['subtask_id']: row['procedure']=attempt['procedure']; row['version']=2
        self.r.task_workflow.save(state,state['phase'])
        self.r.audit.append('context','context.procedure',{'task_id':identifier,'subtask_id':packet['subtask_id'],'attempt_id':attempt['attempt_id']})

    def save_parent(self,identifier,parent_messages):
        if self.store.row(identifier,'planning_messages'):return
        self.store.put(identifier,'planning','planning','planning_messages',parent_messages)
        self.store.put(identifier,'planning','planning','parent_working_memory',self.r.memory.working())
        self.r.audit.append('context','context.saved',{'task_id':identifier,'layer':'planning'})

    def activate(self,identifier):
        slot,name=self.key(identifier)
        previous=getattr(self.local,'key',None)
        if previous!=(identifier,slot):
            self.local.key=(identifier,slot)
            self.r.audit.append('context','context.cleared' if name!='planning' else 'context.restored',{'task_id':identifier,'subtask_id':name})

    def messages(self,identifier,base,packet,parent_messages):
        self.save_parent(identifier,parent_messages)
        self.activate(identifier)
        slot,name=self.key(identifier)
        attempt=self.attempt(identifier)
        if not attempt['messages']:
            if name=='planning': attempt['messages']=self.store.get(identifier,'planning_messages',parent_messages)
            else: attempt['messages']=[*base,{'role':'user','content':canonical({'subtask_id':name,'goal':packet['goal'],'method_from_plan':packet['method_from_plan']})}]
            self.save_attempt(identifier,attempt)
        return list(attempt['messages'])

    def remember(self,identifier,decision,result=None,evidence_id=None):
        attempt=self.attempt(identifier)
        attempt['messages'].append({'role':'assistant','content':canonical(decision)})
        refs=decision.get('knowledge_used_ids',[])
        known=set(self.r.task_workflow.state(identifier).get('knowledge_snapshot',{}).get('node_ids',[]))
        if isinstance(refs,list) and all(isinstance(ref,str) for ref in refs) and set(refs)<=known:
            attempt['used_ids']=list(dict.fromkeys([*attempt['used_ids'],*refs]))
        if result is not None:
            detail={'result':result,'evidence_id':evidence_id}
            if len(canonical(detail).encode())>self.r.core.parameter('tool_result_chars'):
                detail={'result':self.store.reference(identifier,attempt['step_id'],'tool_result',result),'evidence_id':evidence_id}
            attempt['messages'].append({'role':'user','content':canonical(detail)})
        if evidence_id: attempt['evidence_ids'].append(evidence_id)
        self.save_attempt(identifier,attempt)

    def fit(self,identifier,stage,payload):
        limit=1500000
        # 內容還會放入 messages 的字串，計入 JSON 再次跳脫後的大小。
        size=lambda value:len(canonical(canonical(value)).encode())
        if size(payload)<=limit: return payload
        value=dict(payload) if isinstance(payload,dict) else {'data':payload}
        _,name=self.key(identifier)
        for key in sorted(value,key=lambda field:len(canonical(value[field]).encode()),reverse=True):
            value[key]=self.store.reference(identifier,name,'request_part',value[key])
            if size(value)<=limit: return value
        raise DomainError('上下文索引仍超過單次傳輸上限')

    def read(self,identifier,decision,parent=False):
        state=self.r.task_workflow.state(identifier)
        step=self.r.task_workflow.current(identifier) if state and state.get('plan') else None
        allowed=None if parent or step is None else {step['id'],*step['depends_on']}
        reference=decision.get('reference')
        if not isinstance(reference,str): raise DomainError('上下文參照格式無效')
        if reference.startswith('evidence:'):
            row=next((row for row in self.r.permission.evidence(identifier) if row['id']==reference[9:]),None)
            if row is None or allowed is not None and row['step_id'] not in allowed: raise Denied('執行證據不屬於目前子任務的可讀範圍')
            offset=decision.get('offset',0);limit=decision.get('limit',65536)
            if type(offset) is not int or offset<0 or type(limit) is not int or not 1<=limit<=65536: raise DomainError('上下文分頁範圍無效')
            text=canonical(row['result']); page=text[offset:offset+limit]
            return {'reference':reference,'text':page,'offset':offset,'next_offset':offset+len(page) if offset+len(page)<len(text) else None,'total_chars':len(text),'sha256':row['digest']}
        row=self.store.db.one('SELECT step_id,kind FROM task_context_entries WHERE id=? AND task_id=?',(reference,identifier))
        if row and step and not parent and row['step_id']!=step['id'] and row['kind'] not in ('handoff','request_part'):
            raise Denied('子任務不能直接載入其他子任務的原始上下文')
        return self.store.page(reference,identifier,allowed,decision.get('offset',0),decision.get('limit',65536))

    def infer(self,identifier,stage,prompt,payload,complete):
        session,name=self.key(identifier)
        owner='planning' if stage in ('task_intake','planning','plan_review','parent_diagnosis','acceptance') else name
        self.store.put(identifier,owner,'stage','stage_request:'+session+':'+stage,{'prompt':prompt,'payload':payload})
        data=self.fit(identifier,stage,payload)
        messages=[{'role':'system','content':prompt+'\n'+CONTEXT_READ_PROMPT+'\n'+self.r.language.instruction},{'role':'user','content':canonical(data)}]
        for _ in range(self.r.core.parameter('max_steps')):
            stop=getattr(self.r.task_workflow.active,'stop',None)
            if stop and stop.is_set():raise Denied('任務已停止')
            if self.r.permission.task(identifier)['status']!='in_progress':raise Denied('任務目前不能進行推理或驗收')
            if len(canonical(messages).encode())>2*1024*1024:raise DomainError('任務企劃或驗收上下文超過處理上限')
            value=json.loads(complete(messages))
            if not isinstance(value,dict) or value.get('action')!='context_read':
                self.store.put(identifier,owner,'stage','stage_response:'+session+':'+stage,value)
                return value
            page=self.read(identifier,value,parent=stage not in ('procedure','handoff','knowledge_need','knowledge_coverage'))
            messages=[messages[0],messages[1],{'role':'assistant','content':canonical(value)},{'role':'user','content':canonical(page)}]
        raise DomainError('上下文分頁讀取尚未完成，已保留資料')

    def infer_decision(self,identifier,messages,packet):
        reasoning_context=self.r.personality_reasoning.context(identifier) if self.r.has_plugin('auren.personality-reasoning/1') and self.r.emotion.identity().get('mode')=='portrait' else None
        if reasoning_context:
            from auren_core.shaping_prompts import REASONING_RESPONSE
            packet={**packet,'portrait_reasoning':reasoning_context}
            messages=[*messages,{'role':'system','content':REASONING_RESPONSE}]
        actual=[*messages,{'role':'user','content':canonical({'current_context':packet})},
            {'role':'system','content':'表演語氣指引：'+canonical(self.r.emotion.expression())+'\n研究表演授權邊界：'+self.r.emotion.research_guidance()+'\n'+CONTEXT_READ_PROMPT}]
        _,name=self.key(identifier)
        self.store.put(identifier,name,'request','last_request:'+name,actual)
        if len(canonical(actual).encode())>2000000:
            # 完整文字仍在臨時區；傳輸只以具名索引取代大筆資料。
            envelope=self.fit(identifier,'execution',{'messages':actual})
            actual=[*messages[:1],{'role':'system','content':CONTEXT_READ_PROMPT},{'role':'user','content':canonical(envelope)}]
        if len(canonical(actual).encode())>2*1024*1024: raise DomainError('任務企劃或驗收上下文超過處理上限')
        return json.loads(self.r.model().complete(actual))

    def step_evidence(self,identifier,step):
        state=self.r.task_workflow.state(identifier)
        attempt=self.attempt(identifier)
        refs=set(attempt['evidence_ids'])
        snapshot=state.get('knowledge_snapshot',{})
        if snapshot.get('step_id')==step['id']:refs.add(snapshot.get('evidence_id'))
        return [row for row in self.r.permission.evidence(identifier) if row['plan_revision']==state['plan_revision'] and row['step_id']==step['id'] and (row['id'] in refs or row['created_at']>=attempt['created_at'])]

    def handover(self,identifier,step,candidate,checks,passed,blocked=False):
        r=self.r; attempt=self.attempt(identifier)
        result='blocked' if blocked else 'completed' if passed else 'failed'
        proof=r.permission.record_evidence(identifier,step['id'],'acceptance_result',{'candidate':candidate,'checks':checks,'result':result},r.task_workflow.state(identifier)['plan_revision'])
        evidence=self.step_evidence(identifier,step)
        platform={'checks':checks,'candidate':candidate,'evidence_ids':[row['id'] for row in evidence],
            'actions':[{**{key:row[key] for key in ('id','action','created_at','digest')},'result_reference':'evidence:'+row['id']} for row in evidence],
            'attempt_id':attempt['attempt_id'],'plan_revision':r.task_workflow.state(identifier)['plan_revision'],'created_at':now(),'acceptance_evidence_id':proof}
        raw=r.task_workflow.ask('handoff',HANDOFF_PROMPT,{'subtask':step,'procedure':attempt.get('procedure'),'own_context':attempt['messages'],
            'candidate':candidate,'checks':checks,'platform_evidence':evidence,'result':result,'knowledge_used_ids':attempt['used_ids']})
        value=handoff(raw,step,result,evidence,attempt['used_ids'],platform)
        # 已完成項目由真實驗收結果填入，不能由交接模型自報。
        allowed={row['id'] for row in evidence}
        value['completed']=[{'item':row['id'],'evidence_id':next((ref for ref in row['evidence_ids'] if ref in allowed),proof)} for row in checks if row['passed']]
        values=self.handoffs(identifier); values[step['id']]=value
        self.store.put(identifier,step['id'],'handoff','handoff:'+step['id'],value)
        self.store.put(identifier,'planning','handoff_index','handoffs',values)
        parent=self.parent_state(identifier)
        parent.setdefault('accumulated_dead_ends',[]).extend({**row,'subtask_id':step['id'],'source':'本任務'} for row in value['dead_ends'])
        parent['agent_notes']=value['agent_notes'];self.store.put(identifier,'planning','planning','parent',parent)
        r.audit.append('context','context.handoff',{'task_id':identifier,'subtask_id':step['id'],'result':result,'evidence_id':proof})
        r.permission.record_task_event(identifier,'context_handoff',{'text':value['handoff_notes'],'subtask_id':step['id'],'result':result,'decisions':value['decisions'],'unresolved':value['unresolved']})
        self.diagnose_conflicts(identifier,step,value,evidence)
        r.audit.append('context','context.restored',{'task_id':identifier,'subtask_id':step['id'],'reason':'handoff'})
        if passed: return value,None
        diagnosis=self.confirm_failure(identifier,step,value,evidence)
        value['parent_confirmation']=diagnosis;values[step['id']]=value
        self.store.put(identifier,step['id'],'handoff','handoff:'+step['id'],value)
        self.store.put(identifier,'planning','handoff_index','handoffs',values)
        return value,diagnosis

    def pause(self,identifier,decision,message):
        step=self.r.task_workflow.current(identifier)
        if not step or not self.store.row(identifier,self.key(identifier)[0]):return
        proof=self.r.task_workflow.record(identifier,'external_block',{'request':decision,'message':message})
        self.remember(identifier,decision,{'blocked':True,'message':message},proof)
        self.handover(identifier,step,{'id':uid(),'text':message,'created_at':now()},[],False,blocked=True)

    def confirm_failure(self,identifier,step,value,evidence):
        allowed={row['id'] for row in evidence}
        result=self.r.task_workflow.ask('parent_diagnosis',PARENT_DIAGNOSIS_PROMPT,{'parent':self.parent(identifier),'subtask':step,'handoff':value,'platform_evidence':evidence})
        if not isinstance(result,dict) or result.get('inference') not in ('flow_error','method_error','external') or not isinstance(result.get('reason'),str) or not result['reason'].strip(): raise DomainError('大任務層失敗診斷格式無效')
        refs=texts(result.get('evidence_ids'))
        if not refs or not set(refs)<=allowed: raise DomainError('大任務層診斷缺少可查證據')
        if result['inference']=='method_error': string(result.get('method'))
        self.r.audit.append('context','context.failure_confirmed',{'task_id':identifier,'subtask_id':step['id'],'reported':value['flow_diagnosis']['inference'],**result})
        return result

    def apply_failure(self,identifier,step,diagnosis,checks):
        return self.r.components.invoke_flow('flow.replan',lambda:self._apply_failure(identifier,step,diagnosis,checks),
            {'task_id':identifier,'step':step,'diagnosis':diagnosis,'checks':checks})

    def _apply_failure(self,identifier,step,diagnosis,checks):
        r=self.r; kind=diagnosis['inference']
        if kind=='external':
            r.task_workflow.request_clarification(identifier,[diagnosis['reason']]);return
        if kind=='flow_error':
            attempt=self.attempt(identifier)
            attempt['messages'].append({'role':'user','content':canonical({'failure_confirmation':diagnosis,'checks':checks})})
            self.save_attempt(identifier,attempt)
            state=r.task_workflow.state(identifier); state.setdefault('procedure_replan',{})[step['id']]=True
            r.task_workflow.save(state,'executing');return
        state=r.task_workflow.state(identifier); counts=state.setdefault('method_failures',{}); count=counts.get(step['id'],0)+1; counts[step['id']]=count
        state=r.task_workflow.save(state,'executing')
        if count>=5:
            failed={row['id'] for row in checks if not row['passed']}
            detail={'failed_criteria':[row for row in state['plan']['criteria'] if row['id'] in failed],
                'attempted_methods':self.parent(identifier)['accumulated_dead_ends'],
                'latest_evidence':[{key:row[key] for key in ('id','action','digest')} for row in self.step_evidence(identifier,step)]}
            conditions='\n'.join('- '+row['description']+'：'+row['success_condition'] for row in detail['failed_criteria'])
            methods='\n'.join('- '+row['method']+'：'+row['why_failed'] for row in detail['attempted_methods'])
            question='\n'.join((r.language.text('已嘗試 5 次仍無法達成完成條件，請協助調整方法。'),
                r.language.text('仍未滿足的條件：'),conditions,r.language.text('嘗試過的方法與原因：'),methods,
                r.language.text('相關執行紀錄已保留，請提供應對方式，或說明要調整的完成條件。')))
            if len(question)>12000:
                # 求助訊息沿用既有長度契約；完整內容另存，不截斷失敗歷程。
                reference=self.store.reference(identifier,step['id'],'help',detail)
                question=r.language.text('已嘗試 5 次仍無法達成完成條件，請協助調整方法。')+'\n'+r.language.text('完整的完成條件、嘗試與證據已保留。請協助指出要調整的方法或條件。')
                r.permission.record_task_event(identifier,'clarification_context',{'text':question,'detail':reference})
            r.task_workflow.request_clarification(identifier,[question]);return
        # 邏輯節點與驗收契約不變，新的方法實例不沿用舊細節上下文。
        for old in self.store.entries(identifier):
            if old['step_id']==step['id'] and old['kind']!='handoff':self.store.delete(old['id'])
        state.setdefault('method_revisions',{})[step['id']]=state.get('method_revisions',{}).get(step['id'],0)+1
        for row in state['plan']['steps']:
            if row['id']==step['id']: row.update({'goal':diagnosis['method'],'procedure':None,'version':2})
        state['step_status'][step['id']]='pending';r.task_workflow.save(state,'executing')
        r.audit.append('context','context.method_replanned',{'task_id':identifier,'subtask_id':step['id'],'failures':count,'method':diagnosis['method']})

    def diagnose_conflicts(self,identifier,step,value,evidence):
        for index,dead in enumerate(value['dead_ends']):
            knowledge_id=dead['contradicts_knowledge_id']
            if knowledge_id is None: continue
            node=next((row for row in self.r.knowledge.overview()['knowledge'] if row['id']==knowledge_id),None)
            if node is None: raise DomainError('待診斷的知識節點不存在')
            case_id=sha256(canonical([identifier,step['id'],value['platform']['acceptance_evidence_id'],knowledge_id,index]).encode()).hexdigest()
            case={'id':case_id,'task_id':identifier,'subtask_id':step['id'],'knowledge_id':knowledge_id,'knowledge':node,'evidence':evidence,'conflict':dead}
            self.r.events.publish(Event('task.knowledge_conflict',case));self.r.events.dispatch()
            checked=self.r.learning.diagnose_task_knowledge(case_id,
                lambda data:self.r.task_workflow.ask('knowledge_diagnosis',DIAGNOSIS_PROMPT,data),
                lambda data:self.r.task_workflow.ask('knowledge_diagnosis_review',DIAGNOSIS_REVIEW_PROMPT,data))
            parent=self.parent_state(identifier)
            parent.setdefault('knowledge_conflicts',[]).append({'knowledge_id':knowledge_id,'subtask_id':step['id'],'conflict':dead['why_failed'],'diagnostic_id':case_id,'status':checked['status']})
            self.store.put(identifier,'planning','planning','parent',parent);self.r.events.dispatch()

    def persist_diagnosis(self,event):
        value=event.payload
        case=self.r.learning.task_diagnostic(value['id'])
        if case['status']!='confirmed' or case['task_id']!=value['task_id'] or case['knowledge_id']!=value['knowledge_id'] or case['result']!=value['result']:
            raise Denied('知識診斷尚未確認，不能形成跨任務紀錄')
        self.r.memory.append(canonical(value),'procedural','failure_pattern',{'task_id':value['task_id'],'knowledge_id':value['knowledge_id'],'diagnostic_id':value['id']},idempotency_key='knowledge-diagnosis:'+value['id'])

    def summarize(self,identifier,reason):
        if identifier in self.summarizing:return
        self.summarizing.add(identifier)
        try:
            state=self.r.task_workflow.state(identifier)
            if not state:return
            parent=self.parent(identifier) if state.get('plan') else {'initial_brief':state.get('brief')}
            # 格式總結保留任務結構與交接，不把工作記憶原文轉存為永久臨時檔。
            parent.pop('working_memory',None)
            parent.pop('planning_context',None)
            summary={'version':1,'task_id':identifier,'reason':reason,'at':now(),'parent':parent,
                'active_procedures':[{key:value for key,value in self.store.read(row['id'],identifier).items() if key in ('step_id','procedure','used_ids','evidence_ids','plan_revision','method_revision')} for row in self.store.entries(identifier) if row['kind']=='detail']}
            record=self.r.memory.append(canonical(summary),'task','event',{'task_id':identifier,'kind':'context_summary'})
            self.r.core.set_setting('task_context.summary.'+identifier,{'record_id':record,'reason':reason,'created_at':summary['at']})
            self.r.permission.record_task_event(identifier,'context_summary',{'text':self.r.language.text('任務上下文已轉為格式化總結。'),'record_id':record,'reason':reason})
            self.r.audit.append('context','context.summarized',{'task_id':identifier,'reason':reason,'record_id':record})
        finally:self.summarizing.discard(identifier)

    def terminal(self,identifier,status):
        if not self.store.entries(identifier):return
        if status!='done':self.summarize(identifier,status)
        self.store.clear(identifier)
        self.r.audit.append('context','context.cleared',{'task_id':identifier,'reason':status})
        if getattr(self.local,'key',(None,))[0]==identifier:self.local.key=None

    def maintenance(self):
        at=datetime.now(timezone.utc)
        for identifier in {row['task_id'] for row in self.store.entries()}:
            task=self.r.permission.task(identifier)
            if task['status'] in ('done','suspended'):self.terminal(identifier,task['status'])
            elif task['status']=='blocked' and at-datetime.fromisoformat(task['updated_at'])>=timedelta(days=max(1,self.r.core.parameter('context_retention_days'))):
                self.summarize(identifier,'ttl');self.store.clear(identifier)
                self.r.audit.append('context','context.expired',{'task_id':identifier,'reason':'ttl'})
        self.store.quota.enforce()
