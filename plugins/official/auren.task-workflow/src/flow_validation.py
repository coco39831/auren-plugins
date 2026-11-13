"""流程品質裁判在平台側；候選只在原始隔離服務內處理資料。"""
from copy import deepcopy
from dataclasses import asdict
import json
from auren_contracts.models import DomainError,canonical
from auren_contracts.flow import hook
from auren_infra.flow_evaluation import AdversarialSuite,core_cases
from .task_planning import decode_plan
from .task_acceptance import IndependentAcceptance
from auren_core.components import RUNNER


class FlowValidation:
    def __init__(self,runtime):self.r=runtime

    def history(self):
        cases=[]
        references={}
        for row in self.r.security.case_references():
            references.setdefault(row['task_id'],[]).append(row['incident_id'])
        for task in self.r.components.originals['permission'].tasks():
            state=self.r.components.originals['permission'].workflow(task['id'])
            if not state or not state.get('plan') or not state.get('candidate'):continue
            checks=state.get('checks',[])
            if not checks or all(row['passed'] for row in checks):continue
            if any(row['verifier'] not in ('tool_result','output') for row in state['plan']['criteria']):continue
            cases.append({'id':task['id'],'source':'task:'+task['id'],'plan':state['plan'],
                'candidate':state['candidate'],'evidence':self.r.components.originals['permission'].evidence(task['id']),
                'incident_ids':references.get(task['id'],[]),
                'authorized':True,'required_actions':[row['check']['action'] for row in state['plan']['criteria'] if row['verifier']=='tool_result'],'expected':False})
            if len(cases)>=24:break
        return cases

    def evaluate(self,directory,case):
        name=self.module;definition=hook(name);code=(directory/'policy.py').read_text(encoding='utf-8')
        self.r.components.gate.require_agent_execution(code)
        plan=deepcopy(case['plan']);elapsed=0
        brief={'title':'平台案例','goal':plan['understanding'],'objectives':[plan['understanding']],
            'outline':['執行並驗收'],'open_questions':[],'context_ids':[]}
        packet={'version':1,'layer':'detail','subtask_id':'s1','goal':{'criteria':plan['criteria']},
            'method_from_plan':{'goal':plan['understanding']},'knowledge':{'required':False,'context':[],'missing':False}}
        payload={
            'flow.plan':{'task_id':case['id'],'goal':plan['understanding'],'context':{'previous_plan':plan,'available_tools':[]}},
            'flow.intake':{'task_id':case['id'],'original_goal':plan['understanding'],'brief':brief,'context':{'available_context':[]}},
            'flow.step_context':{'task_id':case['id'],'context':packet},
            'flow.knowledge_gate':{'task_id':case['id'],'step_id':'s1','queries':[plan['understanding']],'required':True,'goal':plan['understanding']},
            'flow.acceptance':{'goal':plan['understanding'],'plan':plan,'candidate':case['candidate'],'evidence':case['evidence'],'criteria':None,'confirmed':False},
            'flow.replan':{'task_id':case['id'],'step':plan['steps'][0] if plan['steps'] else {},'diagnosis':{'inference':'method_error'},'checks':[]},
        }[name]
        def policy(stage,result=None):
            nonlocal elapsed
            request={'version':1,'component':name,'operation':'flow','stage':stage,
                'arguments':[{'version':1,'payload':payload}],'keyword_arguments':{},'configuration':{},'result':result}
            executed=self.r.components.sandbox.run({'policy.py':code,'request.json':canonical(request),'invoke.py':RUNNER},['python3','invoke.py'],30)
            elapsed+=executed['elapsed']
            if executed['returncode']:raise DomainError('流程對抗候選執行失敗')
            value=json.loads(executed['stdout'])
            if not isinstance(value,dict) or value.get('mode') not in ('delegate','replace'):raise DomainError('流程對抗候選回覆無效')
            return value
        def replacement(response):
            nonlocal plan
            if not definition.replaceable:raise DomainError('候選試圖取代保護鉤子')
            value=response.get('value')
            if name=='flow.acceptance':
                expected={row['id'] for row in plan['criteria']}
                if not isinstance(value,list) or len(value)!=len(expected) or any(not isinstance(row,dict) or type(row.get('passed')) is not bool or not isinstance(row.get('evidence_ids'),list) for row in value) or {row.get('id') for row in value}!=expected:raise DomainError('候選驗收沒有逐條完成條件與證據')
                return
            if name=='flow.replan':
                if value is not None:raise DomainError('重擬流程不得自行回報完成旗標')
                return
            if not isinstance(value,dict):raise DomainError('候選流程契約無效')
            if name=='flow.plan':
                decode_plan(value)
                declarations={row['check']['action'] for row in value['criteria'] if row['verifier']=='tool_result'}
                if not set(case['required_actions'])<=declarations:raise DomainError('候選省略必要能力證據')
                plan=value
            elif name=='flow.intake':
                if not all(value.get(key) for key in ('title','goal','objectives','outline')):raise DomainError('候選收件企劃不完整')
            elif name=='flow.step_context':
                if any(value.get(key)!=packet[key] for key in packet):raise DomainError('候選省略驗收條件')
        before=policy('before')
        if before['mode']=='replace':replacement(before)
        try:
            decoded=asdict(decode_plan(plan))
            checks=IndependentAcceptance(lambda *args:None).evaluate('',decoded,case['candidate'],case['evidence'])
            accepted=case['authorized'] and all(row['passed'] for row in checks)
        except DomainError:accepted=False;checks=[]
        original={'flow.plan':plan,'flow.intake':brief,'flow.step_context':packet,
            'flow.knowledge_gate':{'required':True,'missing':True,'knowledge':[]},'flow.acceptance':checks,'flow.replan':None}[name]
        after=policy('after',original)
        if after['mode']=='replace':
            replacement(after)
            if name=='flow.plan':
                checks=IndependentAcceptance(lambda *args:None).evaluate('',asdict(decode_plan(plan)),case['candidate'],case['evidence'])
                accepted=case['authorized'] and all(row['passed'] for row in checks)
        return {'accepted':bool(accepted),'elapsed':elapsed}

    def __call__(self,baseline,candidate):
        self.module=baseline.name
        return AdversarialSuite(self.evaluate,core_cases()+self.history())(baseline,candidate)
