"""任務協調器：拆解、強制知識檢索、分步執行、獨立驗收與人工結案。"""
from dataclasses import asdict
from copy import deepcopy
from collections import OrderedDict
from hashlib import sha256
import json
import threading
from auren_contracts.models import Source,Denied,DomainError,RetrievalBudget,Event,canonical,now,uid
from auren_contracts.task_flow import TaskTriggerV1,TaskBriefV1
from .task_planning import IndependentTaskPlanner,decode_plan,strings
from .task_acceptance import IndependentAcceptance
from .task_intake import TaskIntake
from .task_prompts import KNOWLEDGE_NEED_PROMPT,KNOWLEDGE_COVERAGE_PROMPT,PLAN_REVIEW_PROMPT


ACQUISITION=frozenset(('memory','knowledge','knowledge_learn','task_search','task_history','web_search','web_read','web_roam','permission','clarify'))


class TaskWorkflow:
    def __init__(self,runtime):
        self.r=runtime
        self.planner=IndependentTaskPlanner(self.ask,runtime.language.text)
        self.intake=TaskIntake(self.ask,runtime.language.text)
        self.acceptance=IndependentAcceptance(self.ask,runtime.components)
        self.active=threading.local()
        self.state_cache=OrderedDict();self.cache_lock=threading.RLock()
        runtime.permission.watch_workflow(self.invalidate)

    def state(self,identifier):
        """快取解密結果；每次核對持久 CAS 版本，回傳副本避免未儲存變更外洩。"""
        with self.cache_lock:
            revision=self.r.permission.workflow_revision(identifier)
            cached=self.state_cache.pop(identifier,None)
            if revision is None:return None
            if cached is not None and cached['revision']==revision:value=cached
            else:value=self.r.permission.workflow(identifier)
            if value is None:return None
            self.state_cache[identifier]=deepcopy(value)
            # 僅為記憶體快取容量；淘汰後仍從資料庫完整讀回，不裁切工作流程。
            while len(self.state_cache)>64:self.state_cache.popitem(last=False)
            return deepcopy(value)

    def invalidate(self,identifier):
        with self.cache_lock:self.state_cache.pop(identifier,None)

    def ask(self,stage,prompt,payload):
        identifier=getattr(self.active,'task_id',None)
        stop=getattr(self.active,'stop',None)
        if stop and stop.is_set(): raise Denied('任務已停止')
        if identifier and self.r.permission.task(identifier)['status']!='in_progress': raise Denied('任務目前不能進行推理或驗收')
        data=canonical(payload)
        context=getattr(self.r,'task_context',None)
        if not context and len(data.encode())>2*1024*1024: raise DomainError('任務企劃或驗收上下文超過處理上限')
        try:
            value=context.infer(identifier,stage,prompt,payload,lambda messages:self.r.model().complete(messages)) if context and identifier else json.loads(self.r.model().complete([{'role':'system','content':prompt+'\n'+self.r.language.instruction},{'role':'user','content':data}]))
        except (ValueError,TypeError) as exc: raise DomainError('任務拆解或驗收模型未回傳有效 JSON') from exc
        if stop and stop.is_set(): raise Denied('任務已停止')
        if identifier and self.r.permission.task(identifier)['status']!='in_progress': raise Denied('任務目前不能進行推理或驗收')
        self.r.audit.append('task.workflow','task.model_stage',{'task_id':identifier,'stage':stage})
        return value

    def bind(self,identifier,stop=None):
        self.active.task_id=identifier; self.active.stop=stop

    def receipt(self,identifier,kind,record_id=None,conversation_id=None):
        r=self.r; task=r.permission.task(identifier)
        summary={'conversation':'使用者在對話中交付這個任務。','manual':'使用者從任務頁面交付這個任務。',
            'idle':'依已啟用的自動探索安排開始這個任務。',
            'manual_roaming':'使用者手動啟動這次網路探索。','resumed':'既有任務恢復時建立企劃；原始觸發資訊未完整記錄。'}[kind]
        references={'task_id':identifier,'agent_id':r.agent_id}
        if record_id: references['record_id']=record_id
        if conversation_id: references['conversation_id']=conversation_id
        information=[{'id':record_id or identifier,'text':task['goal']['original'],'kind':'recorded_goal' if kind=='resumed' else 'request' if kind in ('conversation','manual') else 'exploration_topic'}]
        if kind=='idle': information.append({'id':'automatic_exploration','text':r.language.text('已啟用自動網路漫遊，且這次符合頻率排程。'),'kind':'event'})
        trigger=TaskTriggerV1(kind,r.language.text(summary),now() if kind=='resumed' else task['created_at'],tuple(information),references)
        return self.intake.draft(task['goal']['original'],trigger)

    def begin(self,identifier,origin,record_id=None,conversation_id=None,require_user=True,kind=None):
        existing=self.r.task_workflow.state(identifier)
        if existing: return existing
        brief=self.receipt(identifier,kind or origin,record_id,conversation_id)
        state=self.r.permission.begin_workflow(identifier,origin,require_user,brief)
        self.r.permission.record_task_event(identifier,'task_brief',{'text':brief.goal,'brief':asdict(brief)})
        return state

    def ensure_receipt(self,identifier):
        r=self.r; state=r.task_workflow.state(identifier)
        if not state.get('brief'):
            state['brief']=asdict(self.receipt(identifier,'resumed'))
            if state.get('plan') and state['phase']!='revision_required':
                # 舊任務只承接現存企劃，不假造當初未記錄的觸發原因。
                state['brief'].update({'objectives':[row['text'] for row in state['plan']['requirements']],
                    'outline':[row['goal'] for row in state['plan']['steps']],'status':'outlined'})
                state['brief_feedback_count']=len(state['feedback'])
                state['plan']['brief_revision']=state['brief']['revision']
            state=self.save(state,state['phase'])
            r.permission.record_task_event(identifier,'task_brief',{'text':state['brief']['goal'],'brief':state['brief']})
        return state

    def outline(self,identifier,context):
        r=self.r; state=self.ensure_receipt(identifier)
        if state['brief']['status']=='outlined' and state.get('brief_feedback_count')==len(state['feedback']):
            if state['brief']['open_questions']:
                self.request_clarification(identifier,state['brief']['open_questions']); return False
            return True
        def validate(value):
            return self.intake.decode(value,state['brief'],context.get('available_context',[]))
        brief=r.components.invoke_flow('flow.intake',lambda:self.intake.outline(r.permission.task(identifier)['goal']['original'],state['brief'],context),
            {'task_id':identifier,'original_goal':r.permission.task(identifier)['goal']['original'],'brief':state['brief'],'context':context},validate)
        content={key:value for key,value in asdict(brief).items() if key in ('title','goal','objectives','outline','open_questions')}
        content['context_ids']=[row['id'] for row in brief.context_information]
        # DTO 的 tuple 經 JSON 邊界轉為清單，與模型回覆及管理端放行使用相同契約。
        content=json.loads(canonical(content))
        return self.accept_outline(identifier,content,state['brief']['revision'],len(state['feedback']))

    def accept_outline(self,identifier,value,brief_revision,feedback_count):
        r=self.r; state=r.task_workflow.state(identifier)
        if not state or not state.get('brief') or state['brief']['revision']!=brief_revision or len(state['feedback'])!=feedback_count:
            raise Denied('初始任務企劃已更新，不能套用過時的內容')
        # 即使經管理端放行，也只採用目標與流程欄位；觸發事實保留平台原始紀錄。
        brief=self.intake.decode(value,state['brief'],self.intake_context(identifier)['available_context'])
        state['brief']=asdict(brief); state['brief_feedback_count']=len(state['feedback'])
        self.save(state,state['phase'])
        r.permission.record_task_event(identifier,'task_brief',{'text':brief.goal,'brief':asdict(brief)})
        r.events.publish(Event('task.brief_ready',{'task_id':identifier,'brief_revision':brief.revision}))
        if brief.open_questions:
            self.request_clarification(identifier,list(brief.open_questions)); return False
        return True

    def intake_context(self,identifier):
        r=self.r; chat=r.core.get_setting('task_chat.'+identifier)
        conversation=json.loads(r.vault.untext(chat)).get('history',[]) if chat else []
        context={'conversation_history':conversation,'human_feedback':r.task_workflow.state(identifier)['feedback']}
        return {**context,'available_context':self.intake.context_information(context)}

    def background_plan(self,identifier,search_only,require_user=False,trigger='idle'):
        r=self.r; state=self.begin(identifier,'automation',require_user=require_user,kind='idle' if trigger=='idle' else 'manual_roaming')
        criteria=[{'id':'record','description':'探索過程已保存','success_condition':'本次探索的實際紀錄已完成，並保留可回查的執行識別碼。','verifier':'tool_result','check':{'action':'background_roaming','step_id':'explore','path':['run','status'],'operator':'equals','expected':'done'}}]
        if not search_only:
            criteria.append({'id':'sources','description':'已實際讀取公開來源','success_condition':'平台保存至少一個這次實際讀取的公開網頁，不以模型宣稱代替。','verifier':'tool_result','check':{'action':'background_roaming','step_id':'explore','path':['read_sources'],'operator':'nonempty'}})
        for criterion in criteria:
            for field in ('description','success_condition'): criterion[field]=r.language.text(criterion[field])
        ids=[criterion['id'] for criterion in criteria]
        plan=decode_plan({'understanding':r.permission.task(identifier)['goal']['original'],'requirements':[{'id':'exploration','text':'依授權探索公開來源，保存過程與成果。','criterion_ids':ids}],
            'steps':[{'id':'explore','title':'探索、整理與保存來源','goal':'按已授權範圍完成這次探索，將未驗證內容保留為候選。','depends_on':[],'criterion_ids':ids,'knowledge_required':False,'knowledge_queries':[],'deliverable':'effect'}],'criteria':criteria,'assumptions':[]})
        value=asdict(plan)
        for row in value['requirements']: row['text']=r.language.text(row['text'])
        for row in value['steps']:
            for field in ('title','goal'): row[field]=r.language.text(row[field])
        brief=state['brief']
        state['brief']=asdict(TaskBriefV1(brief['title'],brief['goal'],tuple(row['text'] for row in value['requirements']),
            tuple(row['goal'] for row in value['steps']),(),TaskTriggerV1(**brief['trigger']),'outlined',brief['revision']+1))
        state['brief_feedback_count']=len(state['feedback'])
        value['brief_revision']=state['brief']['revision']
        state['plan']=value; state['plan_revision']=1; state['step_status']={'explore':'pending'}
        self.save(state,'executing')
        r.permission.record_task_event(identifier,'task_brief',{'text':state['brief']['goal'],'brief':state['brief']})
        r.permission.record_task_event(identifier,'plan',{'text':plan.understanding,'plan':value,'plan_revision':1})

    def finish_background(self,identifier,run,read_sources,summary):
        self.bind(identifier)
        self.record(identifier,'background_roaming',{'run':run,'read_sources':read_sources})
        return self.offer(identifier,summary)

    def save(self,state,phase):
        try:return self.r.permission.save_workflow(state['task_id'],state,phase,state['revision'])
        finally:self.invalidate(state['task_id'])

    def subtasks(self,state):
        return [{'id':step['id'],'title':step['title'],'status':state['step_status'].get(step['id'],'pending'),'depends_on':step['depends_on']} for step in (state.get('plan') or {}).get('steps',[])]

    def block(self,state,message,phase):
        state=self.save(state,phase)
        self.r.permission.update_task(state['task_id'],self.subtasks(state),self.r.language.text(message),'','blocked')
        self.r.events.publish(Event('task.workflow_waiting',{'task_id':state['task_id'],'phase':phase}))
        return state

    def lookup(self,identifier,step_id,queries,required=False,goal=''):
        return self.r.components.invoke_flow('flow.knowledge_gate',lambda:self._lookup(identifier,step_id,queries,required,goal),
            {'task_id':identifier,'step_id':step_id,'queries':queries,'required':required,'goal':goal})

    def _lookup(self,identifier,step_id,queries,required=False,goal=''):
        r=self.r; state=r.task_workflow.state(identifier); nodes={}; tools={}; misses=[]
        r.license.require_feature('knowledge')
        remaining=r.core.parameter('knowledge_tokens')
        for query in list(dict.fromkeys(queries))[:12]:
            if remaining<=0: break
            bundle=r.knowledge.retrieve(query,RetrievalBudget(tokens=remaining,edges=r.core.parameter('knowledge_edges'),steps=r.core.parameter('knowledge_steps')),True)
            remaining-=sum(len(canonical(node).encode()) for node in bundle.knowledge if node['id'] not in nodes)
            nodes.update({node['id']:node for node in bundle.knowledge}); misses.extend(bundle.missing)
            tools.update({row['id']:{key:row.get(key) for key in ('id','name','capabilities','preconditions','io_signatures')} for row in bundle.tools})
        result={'queries':queries,'knowledge':list(nodes.values()),'tools':list(tools.values()),'required':required,'missing':bool(required and not nodes),'dependency_gaps':misses,'gaps':[]}
        digest=sha256(canonical(result).encode()).hexdigest()
        previous=state.get('knowledge_snapshot',{})
        if previous.get('step_id')!=step_id or previous.get('digest')!=digest or previous.get('plan_revision')!=state['plan_revision']:
            if required and nodes:
                coverage=self.ask('knowledge_coverage',KNOWLEDGE_COVERAGE_PROMPT,{'step_goal':goal,'queries':queries,'knowledge':result['knowledge']})
                if not isinstance(coverage,dict) or type(coverage.get('sufficient')) is not bool: raise DomainError('知識證據核對格式無效')
                refs=strings(coverage.get('supporting_ids'),'supporting_ids'); gaps=strings(coverage.get('gaps'),'gaps')
                if not set(refs)<=set(nodes) or coverage['sufficient'] and (not refs or gaps): raise DomainError('知識證據核對引用無效')
                result['missing']=not coverage['sufficient']; result['gaps']=list(gaps)
                result['supporting_ids']=list(refs)
            evidence_id=r.permission.record_evidence(identifier,step_id,'knowledge_context',result,state['plan_revision'])
            state['knowledge_snapshot']={'step_id':step_id,'digest':digest,'plan_revision':state['plan_revision'],'evidence_id':evidence_id,'node_ids':list(nodes),'missing':result['missing'],'gaps':result['gaps'],'supporting_ids':result.get('supporting_ids',[])}
            self.save(state,state['phase'])
        else:
            result.update({key:previous.get(key,[] if key!='missing' else False) for key in ('missing','gaps','supporting_ids')})
        return result

    def prepare(self,identifier):
        r=self.r; state=r.task_workflow.state(identifier)
        if state is None: state=self.begin(identifier,'manual',kind='resumed')
        if state['phase'] in ('awaiting_user','clarification','completed'): return False
        if state['phase']=='accepted': self.finish(identifier); return False
        state=self.ensure_receipt(identifier)
        if state.get('plan') and state['phase']!='revision_required': return True
        context=self.intake_context(identifier); conversation=context['conversation_history']
        goal=r.permission.task(identifier)['goal']['original']
        knowledge=self.lookup(identifier,'planning',[goal])
        if not self.outline(identifier,{'knowledge_context':knowledge,**context}): return False
        state=r.task_workflow.state(identifier)
        if state['phase']=='intake': state=self.save(state,'planning')
        plan_context={'knowledge_context':knowledge,'conversation_history':conversation,
            'initial_brief':state['brief'],
            'working_memory':r.memory.working()['text'],
            'available_tools':[{key:tool.get(key) for key in ('id','name','capabilities','preconditions','io_signatures')} for tool in r.knowledge.overview()['tools'] if tool['status']=='active'],
            'human_feedback':state['feedback'],'previous_plan':state.get('plan'),'previous_execution_evidence':r.permission.evidence(identifier)}
        def validate_plan(value):
            if value.get('questions'):return {'questions':list(strings(value['questions'],'questions',20))}
            candidate=decode_plan(value)
            review=self.ask('plan_review',PLAN_REVIEW_PROMPT,{'original_goal':goal,'initial_brief':state['brief'],'plan':asdict(candidate)})
            if not isinstance(review,dict) or review.get('approved') is not True or review.get('issues')!=[]:raise DomainError('流程覆寫企劃尚未通過獨立審查')
            return candidate
        plan=r.components.invoke_flow('flow.plan',lambda:self.planner.decompose(goal,plan_context),{'task_id':identifier,'goal':goal,'context':plan_context},validate_plan)
        if isinstance(plan,dict):
            self.request_clarification(identifier,plan['questions']); return False
        state=r.task_workflow.state(identifier)
        state['plan']=asdict(plan); state['plan']['brief_revision']=state['brief']['revision']
        state['plan_revision']+=1; state['step_status']={step.id:'pending' for step in plan.steps}
        state['checks']=[]; state['candidate']=None; state['user_confirmation']=None; state['knowledge_needs']={}; state['questions']=[]; state['step_checks']={}; state['step_outputs']={}
        state['method_failures']={}; state['method_revisions']={}; state['procedure_replan']={}
        state=self.save(state,'executing')
        r.permission.record_task_event(identifier,'plan',{'text':plan.understanding,'plan':state['plan'],'plan_revision':state['plan_revision']})
        r.permission.update_task(identifier,self.subtasks(state),r.language.text('任務企劃已建立，開始依序執行。'),'')
        return True

    def current(self,identifier):
        state=self.r.task_workflow.state(identifier)
        for step in state['plan']['steps']:
            if state['step_status'][step['id']]!='done' and all(state['step_status'][parent]=='done' for parent in step['depends_on']): return step
        return None

    def context(self,identifier):
        # 先建立真實知識與子任務快照；覆寫不得刪除或改寫客觀條件與來源。
        packet=self._context(identifier)
        def validate(value):
            for key in ('version','layer','subtask_id','goal','method_from_plan','knowledge','completion_checklist','trigger','subtasks'):
                if key in packet and value.get(key)!=packet[key]:raise DomainError('子任務上下文覆寫不能改寫目標、知識或驗收條件')
            return value
        return self.r.components.invoke_flow('flow.step_context',lambda:packet,{'task_id':identifier,'context':packet},validate)

    def _context(self,identifier):
        r=self.r; state=r.task_workflow.state(identifier); step=self.current(identifier)
        final=step is None
        if final:
            required=any(value.get('required') for value in state.get('knowledge_needs',{}).values()) or any(row['knowledge_required'] for row in state['plan']['steps'])
            queries=[query for row in state.get('knowledge_needs',{}).values() for query in row['queries']]
            step={'id':'final','goal':r.permission.task(identifier)['goal']['original'],'knowledge_required':required,'knowledge_queries':list(dict.fromkeys([*queries,*(query for row in state['plan']['steps'] for query in row['knowledge_queries'])]))[:12]}
        needs=state.setdefault('knowledge_needs',{}).get(step['id'])
        if needs is None:
            assessment=self.ask('knowledge_need',KNOWLEDGE_NEED_PROMPT,{'goal':step['goal'],'step':step})
            if not isinstance(assessment,dict) or type(assessment.get('required')) is not bool: raise DomainError('知識需求判定格式無效')
            needs={'required':step['knowledge_required'] or assessment['required'],'queries':list(dict.fromkeys([*step['knowledge_queries'],*strings(assessment.get('queries',[]),'knowledge queries',12)]))[:12]}
            if needs['required'] and not needs['queries']: needs['queries']=[step['goal']]
            state['knowledge_needs'][step['id']]=needs; self.save(state,'executing')
        knowledge=self.lookup(identifier,step['id'],needs['queries'] or [step['goal']],needs['required'],step['goal'])
        packet=r.task_context.packet(identifier,None if final else step,knowledge)
        if not final:
            current=r.task_workflow.state(identifier); force=current.get('procedure_replan',{}).get(step['id'],False)
            r.task_context.prepare_procedure(identifier,packet,force)
            if force:
                current=r.task_workflow.state(identifier);current['procedure_replan'].pop(step['id'],None);self.save(current,'executing')
            packet=r.task_context.packet(identifier,step,knowledge)
        return packet

    def permit_decision(self,identifier,decision):
        state=self.r.task_workflow.state(identifier); step=self.current(identifier); action=decision.get('action')
        if step is None: step={'id':'final','goal':self.r.permission.task(identifier)['goal']['original']}
        snapshot=state.get('knowledge_snapshot',{}); needs=state.get('knowledge_needs',{}).get(step['id'],{})
        if decision.get('needs_knowledge') is True and not needs.get('required'):
            state.setdefault('knowledge_needs',{})[step['id']]={'required':True,'queries':[str(decision.get('query') or step['goal'])]}
            self.save(state,'executing'); return {'error':'已判定需要知識，必須先檢索知識庫再繼續推理。'}
        if needs.get('required') and snapshot.get('missing') and action not in ACQUISITION:
            return {'error':'知識庫仍有缺口，不能提交成果；請先取得來源並驗證入庫，或請使用者協助補充。'}
        if action in ('respond','step_complete'):
            refs=decision.get('knowledge_used_ids')
            if not isinstance(refs,list) or needs.get('required') and not refs or any(not isinstance(ref,str) for ref in refs) or not set(refs)<=set(snapshot.get('node_ids',[])):
                return {'error':'候選成果必須引用此步驟已檢索的知識庫節點。'}
        return None

    def record(self,identifier,action,result,request=None):
        state=self.r.task_workflow.state(identifier); step=self.current(identifier)
        key=step['id'] if step else 'final'
        evidence_id=self.r.permission.record_evidence(identifier,key,action,result,state['plan_revision'])
        if action in ('knowledge','knowledge_learn') and isinstance(request,dict):
            query=request.get('query') if action=='knowledge' else request.get('claim')
            if isinstance(query,str) and query.strip():
                needs=state.setdefault('knowledge_needs',{}).setdefault(key,{'required':True,'queries':[]})
                needs['required']=True; needs['queries']=list(dict.fromkeys([query,*needs['queries']]))[:12]
                self.save(state,state['phase'])
        return evidence_id

    def request_clarification(self,identifier,questions):
        questions=list(strings(questions,'questions',20))
        if not questions: raise DomainError('任務釐清問題不可空白')
        r=self.r; output='\n'.join(questions)
        state=r.task_workflow.state(identifier); state['questions']=questions
        r.permission.record_task_event(identifier,'clarification',{'text':output})
        r.memory.append(output,'raw','event',{'role':'assistant','task_id':identifier})
        self.block(state,'任務需要先釐清必要資訊。','clarification')
        r.events.publish(Event('agent.response',{'task_id':identifier,'text':output,'clarification':True}))

    def exhausted(self,identifier):
        state=self.r.task_workflow.state(identifier); snapshot=state.get('knowledge_snapshot',{})
        if not snapshot.get('missing'): return False
        detail='; '.join(snapshot.get('gaps',[])) or self.r.permission.task(identifier)['goal']['original']
        question=self.r.language.text('目前在已授權範圍內仍無法補足這些知識：{value}。請提供可驗證的資料，或說明是否調整委託範圍。').format(value=detail)
        self.request_clarification(identifier,[question]); return True

    def step_complete(self,identifier,output):
        if not isinstance(output,str) or not output.strip() or len(output)>32000: raise DomainError('任務候選成果不可空白或超過長度限制')
        r=self.r; state=r.task_workflow.state(identifier); step=self.current(identifier)
        if step is None: return {'accepted':True,'message':'所有子任務已提交，請整理候選成果。'}
        candidate={'id':uid(),'text':output,'created_at':now(),'plan_revision':state['plan_revision']}
        selected=[criterion for criterion in state['plan']['criteria'] if criterion['id'] in step['criterion_ids']]
        checks=self.acceptance.evaluate(r.permission.task(identifier)['goal']['original'],state['plan'],candidate,r.permission.evidence(identifier),selected)
        # 主觀條件留待最後使用者確認；客觀條件不能略過。
        objective={criterion['id'] for criterion in selected if criterion['verifier']!='user'}
        passed=all(row['passed'] for row in checks if row['id'] in objective)
        managed=bool(r.task_context.store.row(identifier,r.task_context.key(identifier)[0]))
        diagnosis=None
        if managed:
            _,diagnosis=r.task_context.handover(identifier,step,candidate,checks,passed)
            state=r.task_workflow.state(identifier)
        r.permission.record_task_event(identifier,'step_acceptance',{'text':step['title'],'checks':checks,'candidate':candidate})
        state.setdefault('step_checks',{})[step['id']]=checks
        if passed:
            state['step_status'][step['id']]='done'; state.setdefault('step_outputs',{})[step['id']]=candidate
        self.save(state,'executing')
        if passed:
            r.permission.update_task(identifier,self.subtasks(state),r.language.text('子任務已核對，繼續處理下一步。'),'')
        elif diagnosis: r.task_context.apply_failure(identifier,step,diagnosis,checks)
        return {'accepted':passed,'checks':checks,'remaining_step_ids':[key for key,value in state['step_status'].items() if value!='done']}

    def offer(self,identifier,output,candidate_id=None):
        if not isinstance(output,str) or not output.strip() or len(output)>32000: raise DomainError('任務候選成果不可空白或超過長度限制')
        r=self.r; state=r.task_workflow.state(identifier)
        if self.current(identifier):
            managed=bool(r.task_context.store.row(identifier,r.task_context.key(identifier)[0]))
            result=self.step_complete(identifier,output)
            if managed or not result['accepted'] or self.current(identifier): return {'ready':False,**result}
        state=r.task_workflow.state(identifier); candidate={'id':candidate_id or uid(),'text':output,'created_at':now(),'plan_revision':state['plan_revision']}
        state['candidate']=candidate; state['user_confirmation']=None; state=self.save(state,'verifying')
        checks=self.acceptance.evaluate(r.permission.task(identifier)['goal']['original'],state['plan'],candidate,r.permission.evidence(identifier))
        state=r.task_workflow.state(identifier); state['checks']=checks
        objective={criterion['id'] for criterion in state['plan']['criteria'] if criterion['verifier']!='user'}
        passed=all(row['passed'] for row in checks if row['id'] in objective)
        r.permission.record_task_event(identifier,'acceptance',{'text':r.language.text('已逐條核對候選成果。'),'candidate':candidate,'checks':checks})
        if not passed:
            failed={row['id'] for row in checks if row['id'] in objective and not row['passed']}
            for step in state['plan']['steps']:
                if set(step['criterion_ids'])&failed: state['step_status'][step['id']]='pending'
            self.save(state,'executing'); return {'ready':False,'checks':checks}
        r.memory.append(output,'raw','event',{'role':'assistant','task_id':identifier,'candidate_id':candidate['id']},idempotency_key='candidate:'+candidate['id'])
        self.r.events.publish(Event('agent.response',{'task_id':identifier,'text':output,'awaiting_acceptance':True}))
        if state['require_user'] or any(criterion['verifier']=='user' for criterion in state['plan']['criteria']):
            self.block(state,'候選成果已送達，請逐條確認完成條件是否都已滿足。','awaiting_user')
            return {'ready':True,'awaiting_user':True}
        self.save(state,'accepted'); self.finish(identifier); return {'ready':True,'completed':True}

    def feedback(self,identifier,accepted,feedback,actor,revision,candidate_id):
        if actor.source!=Source.HUMAN or type(accepted) is not bool: raise Denied('任務完成確認只接受使用者回答')
        if not isinstance(feedback,str) or len(feedback)>32000: raise DomainError('任務回饋內容無效')
        r=self.r
        with r.lock:
            state=r.task_workflow.state(identifier)
            if not state or r.permission.task(identifier)['status']!='blocked' or state['phase']!='awaiting_user' or state['revision']!=revision or not state['candidate'] or state['candidate']['id']!=candidate_id:
                raise Denied('確認不對應目前待驗收的任務成果，請重新載入')
            r.permission.record_task_event(identifier,'user_acceptance',{'text':feedback,'accepted':accepted,'subject':actor.subject,'candidate_id':candidate_id})
            r.memory.append(feedback or r.language.text('確認完成' if accepted else '尚未完成'),'raw','event',{'role':'user','task_id':identifier})
            if not accepted:
                state['feedback'].append({'text':feedback,'accepted':False,'at':now()}); state['user_confirmation']=None; state['checks']=[]
                self.save(state,'revision_required')
                return {'completed':False,'revision_required':True,'task':r.permission.task(identifier)}
            criteria={criterion['id']:criterion for criterion in state['plan']['criteria']}
            state['checks']=[{**row,'passed':True,'reason':'使用者已確認此項完成條件。'} if criteria[row['id']]['verifier']=='user' else row for row in state['checks']]
            state['user_confirmation']={'accepted':True,'subject':actor.subject,'candidate_id':candidate_id,'feedback':feedback,'at':now()}
            self.save(state,'accepted'); self.finish(identifier)
            return {'completed':True,'task':r.permission.task(identifier)}

    def clarify(self,identifier,answer,actor,revision):
        if actor.source!=Source.HUMAN or not isinstance(answer,str) or not answer.strip() or len(answer)>32000: raise Denied('任務釐清需要使用者的明確回答')
        state=self.r.task_workflow.state(identifier)
        if not state or self.r.permission.task(identifier)['status']!='blocked' or state['phase']!='clarification' or state['revision']!=revision: raise Denied('任務企劃或驗收狀態已更新，請重新載入')
        state['feedback'].append({'text':answer,'at':now(),'kind':'clarification'}); state['questions']=[]
        self.save(state,'revision_required')
        self.r.memory.append(answer,'raw','event',{'role':'user','task_id':identifier})
        self.r.permission.record_task_event(identifier,'clarification_answer',{'text':answer,'subject':actor.subject})

    def finish(self,identifier):
        r=self.r; state=r.task_workflow.state(identifier)
        if r.permission.task(identifier)['status']=='done':
            r.permission.seal_workflow(identifier); r.task_context.terminal(identifier,'done'); return
        r.permission.require_completion(identifier)
        output=state['candidate']['text']
        r.memory.append(output,'diary','event',{'period':'task','task_id':identifier,'neutral':not r.emotion.expression()['enabled']},idempotency_key='accepted:'+state['candidate']['id'])
        working=r.memory.working(); r.memory.write_working(output[:6000],working['version'])
        r.permission.update_task(identifier,self.subtasks(state),output,output[:6000],'done')
        # 已完成的任務不再走一般可變狀態入口；只固定終態標記。
        r.permission.seal_workflow(identifier)
        r.task_context.terminal(identifier,'done')
        try: r.growth.reflect(identifier,len(r.permission.evidence(identifier)))
        except DomainError: r.audit.append('growth','growth.deferred',{'task_id':identifier})
        r.events.publish(Event('task.accepted',{'task_id':identifier,'candidate_id':state['candidate']['id']}))

    def answer(self,identifier,answer,actor):
        state=self.r.task_workflow.state(identifier)
        if not state: raise Denied('任務尚未建立驗收流程')
        if state['phase']=='clarification':
            self.clarify(identifier,answer,actor,state['revision'])
            return self.r.resume_agent_task(identifier)
        if state['phase']!='awaiting_user': raise Denied('目前任務尚未進入使用者驗收階段')
        normalized=answer.strip().lower().rstrip('。.!！ ')
        positive={'完成','完成了','確認完成','可以結案','滿意','好','是','yes','done','accepted','i confirm completion','完了','完了です','確認しました','はい','완료','완료했어요','확인했습니다','네'}
        negative=('尚未完成','還沒完成','沒有完成','不滿意','未完成','not done','not complete','no','まだ','完了していない','いいえ','미완료','아직','아니요')
        if normalized in positive: accepted=True
        elif any(normalized.startswith(value) for value in negative): accepted=False
        else:
            self.r.memory.append(answer,'raw','event',{'role':'user','task_id':identifier})
            self.r.permission.record_task_event(identifier,'acceptance_clarification',{'text':answer,'subject':actor.subject})
            self.r.permission.update_task(identifier,self.subtasks(state),self.r.language.text('請明確確認所有完成條件是否已滿足，或說明尚未完成的部分。'),'','blocked')
            return self.r.permission.task(identifier)
        result=self.feedback(identifier,accepted,answer,actor,state['revision'],state['candidate']['id'])
        return self.r.resume_agent_task(identifier) if result.get('revision_required') else result['task']

    def learn(self,identifier,decision,stop):
        r=self.r; sources=decision.get('sources'); claim=decision.get('claim'); summary=decision.get('summary')
        if not isinstance(sources,list) or not 1<=len(sources)<=12 or any(not isinstance(url,str) for url in sources) or not isinstance(claim,str) or not claim.strip() or not isinstance(summary,str) or not summary.strip():
            raise DomainError('知識補全必須提供主張、摘要與實際讀取的來源')
        r.license.require_feature('learning'); r.license.require_feature('knowledge')
        read={row['result']['url']:row['result']['text'] for row in r.permission.evidence(identifier) if row['action']=='web_read' and isinstance(row['result'],dict) and isinstance(row['result'].get('url'),str) and isinstance(row['result'].get('text'),str)}
        if not set(sources)<=set(read): raise Denied('知識補全不能引用本任務未實際讀取的來源')
        candidate=r.learning.submit(claim,summary,r.language.code,[{'url':url} for url in sources])
        try:
            result=r.learning.verify(candidate,identifier,fetch=lambda url,task:read[url],cancelled=lambda:stop.is_set() or r.permission.task(identifier)['status']!='in_progress')
        except DomainError as exc:
            if stop.is_set() or r.permission.task(identifier)['status']!='in_progress': raise
            return {'candidate_id':candidate,'status':'unverified','error':str(exc)}
        # 跨模組入庫仍走既有事件匯流排，不直接寫入知識資料表。
        if result['status']=='verified': r.events.dispatch()
        return {'candidate_id':candidate,**result}
