"""歷史重跑只重播已保存的觀測，絕不重送外部操作；結果只是參考指標。"""
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import Path
import shutil
import tempfile
from auren_contracts.models import Denied,DomainError,Source,canonical,now,uid
from auren_core.components import RUNNER,dto
from auren_core.flow_hooks import FlowHooks
from auren_core.gate import FLOW
from .task_acceptance import IndependentAcceptance
from .task_intake import TaskIntake
from .task_planning import IndependentTaskPlanner,decode_plan
from .task_prompts import PLAN_REVIEW_PROMPT
from types import SimpleNamespace


REPLAY_PROMPT='''這是只讀的歷史任務重跑，不是真實執行。依任務目標及目前流程決定下一步。只回傳 JSON。
action=respond 時提供 text；需要工具時 action 與必要欄位沿用原任務協定。平台只會回傳與歷史請求完全相符的已保存觀測，沒有紀錄的操作不執行；缺必要資訊用 clarify、questions。不能把歷史結果當成重新完成的外部工作。'''


class Preview(FlowHooks):
    def __init__(self,runtime,module,directory,host):
        self.r,self.module,self.directory=runtime,module,directory;self.failed=False
        self.runtime=SimpleNamespace(plugin_host=host,agent_id=runtime.agent_id)
    def _execute(self,component,operation,args,kwargs,stage,result=None):
        if self.failed or component!=self.module:return {'mode':'delegate'}
        path=self.directory/'policy.py'
        if not path.exists():return {'mode':'delegate'}
        code=path.read_text(encoding='utf-8');self.r.components.gate.require_agent_execution(code)
        request={'version':1,'component':component,'operation':operation,'stage':stage,'arguments':dto(args),'keyword_arguments':dto(kwargs),'configuration':{},'result':dto(result)}
        executed=self.r.components.sandbox.run({'policy.py':code,'request.json':canonical(request),'invoke.py':RUNNER},['python3','invoke.py'],30)
        if executed['returncode']:raise DomainError('歷史重跑的候選流程執行失敗')
        value=json.loads(executed['stdout'])
        if not isinstance(value,dict) or value.get('mode') not in ('delegate','replace'):raise DomainError('歷史重跑的候選回覆無效')
        return value
    def _fallback(self,component,error):self.failed=True


class FlowReplay:
    def __init__(self,runtime):self.r=runtime
    def tasks(self):
        # 使用擁有資料的 Service；不跨模組讀取資料表。
        result=[];permission=self.r.components.originals['permission']
        for task in permission.tasks():
            state=permission.workflow(task['id'])
            if task['status'] not in ('done','suspended','blocked') or not state or not state.get('plan'):continue
            events=[];offset=0
            while True:
                page=permission.task_history(task['id'],offset,100);events.extend(page['events']);offset+=len(page['events'])
                if offset>=page['total'] or not page['events']:break
            result.append({'task':task,'workflow':state,'evidence':permission.evidence(task['id']),'events':events})
        return result

    def run_task(self,item,module,directory):
        with self.r.plugin_host.preview('auren.official.flow') as host:
            hooks=Preview(self.r,module,directory,host)
            result=self._run_task(item,module,directory,hooks)
        if hooks.failed:result.update({'status':'unavailable','objective_passed':False,'override_failed':True,'reason':'隔離候選未成功執行，不能以內建回退結果冒充候選實測'})
        return result

    def _run_task(self,item,module,directory,hooks):
        r=self.r;state=deepcopy(item['workflow']);task=item['task'];goal=task['goal']['original']
        def ask(stage,prompt,payload):
            data=canonical(payload)
            if len(data.encode())>2*1024*1024:raise DomainError('歷史重跑上下文超過傳輸上限')
            return json.loads(r.model().complete([{'role':'system','content':prompt+'\n'+r.language.instruction},{'role':'user','content':data}]))
        brief=state.get('brief');context={'available_context':[],'human_feedback':state.get('feedback',[])}
        if brief:
            intake=TaskIntake(ask,r.language.text)
            outlined=hooks.invoke_flow('flow.intake',lambda:intake.outline(goal,brief,context),
                {'task_id':task['id'],'original_goal':goal,'brief':brief,'context':context},lambda value:intake.decode(value,brief,[]))
            context['initial_brief']=asdict(outlined)
            if outlined.open_questions:return {'status':'requires_human','objective_passed':False,'reason':'需要歷史紀錄外的使用者補充'}
        planner=IndependentTaskPlanner(ask,r.language.text)
        def validate_plan(value):
            decoded=decode_plan(value)
            review=ask('plan_review',PLAN_REVIEW_PROMPT,{'original_goal':goal,'plan':asdict(decoded)})
            if not isinstance(review,dict) or review.get('approved') is not True or review.get('issues')!=[]:raise DomainError('歷史候選企劃未通過獨立審查')
            return decoded
        plan=hooks.invoke_flow('flow.plan',lambda:planner.decompose(goal,context),{'task_id':task['id'],'goal':goal,'context':context},validate_plan)
        if isinstance(plan,dict):return {'status':'requires_human','objective_passed':False,'reason':'企劃仍需釐清'}
        plan=asdict(plan);recorded=item['evidence'];observed=[];messages=[]
        requests={};pending=None;by_event={row['event_id']:row for row in recorded}
        for event in item['events']:
            if event['kind']=='attempt':pending=event.get('value',{}).get('request')
            if event['kind']=='evidence' and isinstance(pending,dict):
                recorded_row=by_event.get(event['id'])
                if recorded_row and recorded_row['action']==pending.get('action'):
                    requests.setdefault(canonical(pending),[]).append(recorded_row)
                    pending=None
        consumed=set();failure_count=0
        knowledge=[row['result'] for row in recorded if row['action']=='knowledge_context']
        for index in range(r.core.parameter('max_steps')):
            knowledge_result=hooks.invoke_flow('flow.knowledge_gate',lambda:{'records':knowledge,'missing':any(step['knowledge_required'] for step in plan['steps']) and not knowledge},
                {'task_id':task['id'],'queries':[goal],'required':any(step['knowledge_required'] for step in plan['steps'])})
            if knowledge_result['missing']:return {'status':'unavailable','objective_passed':False,'reason':'歷史知識不足，不執行新的補找操作'}
            packet={'version':1,'layer':'planning','objective':goal,'plan':plan,'knowledge':knowledge_result,'recorded_observations':observed}
            def validate(value):
                if any(value.get(key)!=packet[key] for key in ('version','layer','objective','plan','knowledge')):raise DomainError('歷史重跑上下文不得改寫驗收條件')
                return value
            packet=hooks.invoke_flow('flow.step_context',lambda:packet,{'task_id':task['id'],'context':packet},validate)
            decision=ask('replay',REPLAY_PROMPT,{'current_context':packet,'messages':messages})
            action=decision.get('action')
            if action=='clarify':return {'status':'requires_human','objective_passed':False,'reason':'需要使用者補充'}
            if action=='respond':
                candidate={'id':'replay:'+str(index),'text':decision.get('text',''),'plan_revision':state['plan_revision']}
                checks=IndependentAcceptance(ask,hooks).evaluate(goal,plan,candidate,observed)
                objective=[row for row in checks if next(c['verifier'] for c in plan['criteria'] if c['id']==row['id'])!='user']
                if not objective:return {'status':'requires_human','objective_passed':False,'reason':'僅有使用者驗收條件，不能由歷史重跑代替確認'}
                if all(row['passed'] for row in objective):return {'status':'evaluated','objective_passed':True,'checks':checks,'requires_new_user_acceptance':any(row['verifier']=='user' for row in plan['criteria']),'override_failed':hooks.failed}
                def replan():
                    nonlocal failure_count
                    failure_count+=1
                hooks.invoke_flow('flow.replan',replan,{'task_id':task['id'],'checks':checks})
                if failure_count>=5:return {'status':'blocked','objective_passed':False,'reason':'重擬五次仍未通過','checks':checks}
                messages.append({'action':'respond','checks':checks});continue
            matches=requests.get(canonical(decision),[])
            # 未保存完全相同的請求，不能用相似操作的舊結果冒充本次觀測。
            matching=next((row for row in matches if row['id'] not in consumed),None)
            if matching is None:return {'status':'unavailable','objective_passed':False,'reason':'缺少相同操作的歷史觀測；未重送外部操作','action':action}
            consumed.add(matching['id']);observed.append(deepcopy(matching));messages.append({'request':decision,'historical_result':matching['result'],'source_evidence_id':matching['id']})
        return {'status':'blocked','objective_passed':False,'reason':'達到既有單次步數上限'}

    def report(self,identifier,actor,task_ids=None):
        if actor.source!=Source.HUMAN:raise Denied('歷史實測需由使用者明確啟動')
        self.r.require_activated();self.r.license.require_feature('selfmod');proposal=self.r.selfmod.proposal(identifier)
        if proposal['module'] not in FLOW:raise Denied('歷史實測只接受任務流程提案')
        selected=self.tasks()
        if task_ids is not None:
            if not isinstance(task_ids,list) or not all(isinstance(item,str) for item in task_ids):raise DomainError('歷史任務選擇無效')
            known={row['task']['id'] for row in selected}
            if not set(task_ids)<=known:raise Denied('指定的歷史任務不可用')
            selected=[row for row in selected if row['task']['id'] in task_ids]
        # 未指定時只取近期十筆；使用者可明確選擇更多，避免暗中大量呼叫模型。
        else:selected=selected[:10]
        base=self.r.selfmod._path(proposal['module'])
        if self.r.selfmod._digest(base)!=proposal['base_hash']:raise Denied('流程基準已變更，請重新提案')
        report={'version':1,'proposal_id':identifier,'created_at':now(),'reference_only':True,'external_operations_replayed':False,'sample_count':len(selected),'rows':[],'conclusion':'insufficient_samples'}
        with tempfile.TemporaryDirectory(prefix='flow-replay-',dir=self.r.root) as location:
            candidate=Path(location);shutil.copytree(base,candidate,dirs_exist_ok=True)
            for change in proposal['changes']:
                path=candidate/change['path']
                if not path.resolve().is_relative_to(candidate.resolve()):raise Denied('歷史實測候選路徑越界')
                path.parent.mkdir(parents=True,exist_ok=True);path.write_text(change['after'],encoding='utf-8')
            for item in selected:
                row={'task_id':item['task']['id'],'history_sha256':sha256(canonical(item).encode()).hexdigest()}
                for side,directory in (('baseline',base),('candidate',candidate)):
                    try:row[side]=self.run_task(item,proposal['module'],directory)
                    except (DomainError,ValueError,OSError) as exc:row[side]={'status':'unavailable','objective_passed':False,'reason':str(exc)}
                report['rows'].append(row)
        eligible=[row for row in report['rows'] if all(row[side]['status'] in ('evaluated','blocked') for side in ('baseline','candidate'))]
        report['comparable_samples']=len(eligible)
        for side in ('baseline','candidate'):report[side]={'objective_success_rate':sum(row[side]['objective_passed'] for row in eligible)/len(eligible) if eligible else None}
        if len(eligible)>=10:report['conclusion']='regression_observed' if report['candidate']['objective_success_rate']<report['baseline']['objective_success_rate'] else 'no_regression_observed'
        report['sample_policy']='至少十筆可比任務才顯示觀測結論；不構成統計顯著性或自動拒絕依據'
        reference=self.r.memory.append(canonical(report),'log','event',{'kind':'flow_replay','proposal_id':identifier})
        report['record_id']=reference;self.r.core.set_setting('flow.replay.'+identifier,self.r.vault.text(canonical(report)))
        self.r.audit.append(actor.subject,'flow.historical_replay',{'proposal_id':identifier,'record_id':reference,'sample_count':len(selected)})
        return report
