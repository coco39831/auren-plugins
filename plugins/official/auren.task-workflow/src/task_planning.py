"""獨立拆解及靜態企劃契約驗證；無有效企劃不得執行。"""
from dataclasses import asdict
from auren_contracts.models import DomainError,canonical
from auren_contracts.task_flow import TaskPlanV1,TaskStepV1,TaskStepV2,AcceptanceCriterionV1
from .task_prompts import PLAN_PROMPT,PLAN_REVIEW_PROMPT


def text(value,label):
    if not isinstance(value,str) or not value.strip() or len(value)>16000: raise DomainError(f'任務企劃欄位無效：{label}')
    return value.strip()


def strings(value,label,maximum=128):
    if not isinstance(value,list) or len(value)>maximum: raise DomainError(f'任務企劃清單無效：{label}')
    return tuple(text(item,label) for item in value)


def decode_plan(value):
    if not isinstance(value,dict): raise DomainError('任務企劃必須是資料物件')
    raw_steps=value.get('steps'); raw_criteria=value.get('criteria'); requirements=value.get('requirements')
    version=value.get('version',1)
    if type(version) is not int or version not in (1,2): raise DomainError('任務企劃契約版本不受支援')
    if not isinstance(raw_steps,list) or not 1<=len(raw_steps)<=64 or not isinstance(raw_criteria,list) or not 1<=len(raw_criteria)<=128 or not isinstance(requirements,list) or not 1<=len(requirements)<=128:
        raise DomainError('任務企劃必須包含需求、子任務與詳細完成條件')
    criteria=[]
    for row in raw_criteria:
        if not isinstance(row,dict) or row.get('verifier') not in ('output','tool_result','independent_review','user') or not isinstance(row.get('check',{}),dict):
            raise DomainError('任務完成條件的驗收方式無效')
        check=row.get('check',{})
        if row['verifier'] in ('output','tool_result') and check.get('operator') not in ('contains','equals','json_keys','nonempty'):
            raise DomainError('任務完成條件的檢查運算無效')
        if row['verifier'] in ('output','tool_result') and check['operator']!='nonempty' and 'expected' not in check:
            raise DomainError('任務完成條件缺少預期結果')
        if row['verifier'] in ('output','tool_result') and check.get('operator')=='contains' and (not isinstance(check['expected'],(str,list)) or not check['expected'] or isinstance(check['expected'],list) and any(not isinstance(item,str) or not item for item in check['expected'])):
            raise DomainError('任務完成條件的預期文字無效')
        if row['verifier'] in ('output','tool_result') and check.get('operator')=='json_keys' and (not isinstance(check['expected'],list) or not check['expected'] or any(not isinstance(item,str) or not item for item in check['expected'])):
            raise DomainError('任務完成條件的 JSON 欄位無效')
        if row['verifier']=='tool_result' and (not isinstance(check.get('path'),list) or len(check['path'])>12 or any(type(p) not in (str,int) for p in check['path']) or not isinstance(check.get('action'),str) or not isinstance(check.get('step_id'),str)):
            raise DomainError('執行證據條件缺少動作、子任務或欄位路徑')
        if row['verifier']=='independent_review' and check.get('scope') not in ('output','execution'):
            raise DomainError('獨立驗收條件未指定成果或執行證據範圍')
        criteria.append(AcceptanceCriterionV1(text(row.get('id'),'criterion.id'),text(row.get('description'),'description'),text(row.get('success_condition'),'success_condition'),row['verifier'],check))
    ids={row.id for row in criteria}
    if len(ids)!=len(criteria): raise DomainError('任務企劃識別碼不可重複')
    steps=[]
    for row in raw_steps:
        if not isinstance(row,dict) or type(row.get('knowledge_required')) is not bool or row.get('deliverable') not in ('reply','artifact','effect'):
            raise DomainError('子任務知識需求或交付種類無效')
        queries=strings(row.get('knowledge_queries',[]),'knowledge_queries',12)
        checks=strings(row.get('criterion_ids'),'criterion_ids')
        if not checks or not set(checks)<=ids or row['knowledge_required'] and not queries:
            raise DomainError('子任務必須列明完成條件與必要知識檢索詞')
        if row['deliverable']!='reply' and not any(c.id in checks and c.verifier in ('tool_result','user') for c in criteria):
            raise DomainError('外部成果必須以真實執行證據或使用者確認驗收')
        step_version=row.get('version',version)
        if type(step_version) is not int or step_version not in (1,2): raise DomainError('子任務契約版本不受支援')
        if step_version==2 and row.get('procedure') is not None: raise DomainError('子任務流程只能由子任務層擬定')
        step_type=TaskStepV2 if step_version==2 else TaskStepV1
        steps.append(step_type(text(row.get('id'),'step.id'),text(row.get('title'),'step.title'),text(row.get('goal'),'step.goal'),strings(row.get('depends_on',[]),'depends_on',64),checks,row['knowledge_required'],queries,row['deliverable']))
    step_ids={step.id for step in steps}
    if len(step_ids)!=len(steps): raise DomainError('任務企劃識別碼不可重複')
    if step_ids&{'planning','final'}: raise DomainError('子任務使用了平台保留的識別碼')
    if any(not set(step.depends_on)<=step_ids or step.id in step.depends_on for step in steps): raise DomainError('子任務依賴無效')
    visited=set()
    while len(visited)<len(steps):
        ready={step.id for step in steps if step.id not in visited and set(step.depends_on)<=visited}
        if not ready: raise DomainError('子任務依賴形成循環')
        visited.update(ready)
    requirement_ids=set(); covered=set()
    for row in requirements:
        if not isinstance(row,dict): raise DomainError('任務需求格式無效')
        identifier=text(row.get('id'),'requirement.id'); text(row.get('text'),'requirement.text')
        refs=strings(row.get('criterion_ids'),'requirement.criterion_ids')
        if identifier in requirement_ids or not refs or not set(refs)<=ids: raise DomainError('每條任務需求都必須對應有效完成條件')
        requirement_ids.add(identifier); covered.update(refs)
    if covered!=ids or set(c for step in steps for c in step.criterion_ids)!=ids:
        raise DomainError('任務需求、子任務與完成條件必須完整對應')
    if any(c.verifier=='tool_result' and c.check['step_id'] not in step_ids for c in criteria): raise DomainError('執行證據條件引用未知子任務')
    ancestors={step.id:set(step.depends_on) for step in steps}
    for _ in steps:
        for step in steps:
            ancestors[step.id].update(parent for dependency in step.depends_on for parent in ancestors[dependency])
    for step in steps:
        if any(c.id in step.criterion_ids and c.verifier=='tool_result' and c.check['step_id'] not in ancestors[step.id]|{step.id} for c in criteria):
            raise DomainError('子任務的完成條件不能依賴尚未執行的後續工作')
    return TaskPlanV1(text(value.get('understanding'),'understanding'),tuple(requirements),tuple(steps),tuple(criteria),strings(value.get('assumptions',[]),'assumptions',64),version=version)


class IndependentTaskPlanner:
    def __init__(self,ask,localize=lambda value:value): self.ask=ask; self.localize=localize

    def decompose(self,goal,context):
        corrections=[]
        for _ in range(3):
            proposed=self.ask('planning',PLAN_PROMPT,{'original_goal':goal,**context,'review_corrections':corrections})
            if isinstance(proposed,dict) and proposed.get('questions'):
                return {'questions':list(strings(proposed['questions'],'questions',20))}
            try: plan=decode_plan(proposed)
            except DomainError as exc:
                corrections=[self.localize(str(exc))]; continue
            review=self.ask('plan_review',PLAN_REVIEW_PROMPT,{'original_goal':goal,'initial_brief':context.get('initial_brief'),'conversation_history':context.get('conversation_history',[]),'human_feedback':context.get('human_feedback',[]),'plan':asdict(plan)})
            if isinstance(review,dict) and review.get('approved') is True and review.get('issues')==[]: return plan
            corrections=review.get('issues',[]) if isinstance(review,dict) else ['企劃審查回應格式不符。']
        return {'questions':[self.localize('企劃尚未通過獨立審查，請協助釐清：{value}').format(value=canonical(corrections))]}
