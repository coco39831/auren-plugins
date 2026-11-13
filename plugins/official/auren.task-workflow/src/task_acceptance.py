"""平台證據與獨立驗收；執行模型回傳的完成旗標不具驗收效力。"""
import json
from .task_prompts import ACCEPTANCE_PROMPT
from auren_contracts.models import DomainError,canonical,now

MISSING=object()


def compare(actual,check):
    if actual is MISSING: return False
    operator=check.get('operator'); expected=check.get('expected')
    if operator=='nonempty': return actual is not None and (not isinstance(actual,str) or bool(actual.strip())) and actual!=[] and actual!={}
    if operator=='equals': return canonical(actual)==canonical(expected)
    if operator=='contains':
        terms=expected if isinstance(expected,list) else [expected]
        return isinstance(actual,str) and bool(terms) and all(isinstance(value,str) and value and value in actual for value in terms)
    if operator=='json_keys':
        if isinstance(actual,str):
            try: actual=json.loads(actual)
            except ValueError: return False
        return isinstance(actual,dict) and isinstance(expected,list) and bool(expected) and all(isinstance(key,str) and key in actual for key in expected)
    return False


def field(value,path):
    for key in path:
        if isinstance(value,dict) and isinstance(key,str) and key in value: value=value[key]
        elif isinstance(value,list) and type(key) is int and 0<=key<len(value): value=value[key]
        else: return MISSING
    return value


class IndependentAcceptance:
    def __init__(self,ask,hooks=None): self.ask=ask; self.hooks=hooks

    def evaluate(self,goal,plan,candidate,evidence,criteria=None,confirmed=False):
        if self.hooks:
            return self.hooks.invoke_flow('flow.acceptance',lambda:self._evaluate(goal,plan,candidate,evidence,criteria,confirmed),
                {'goal':goal,'plan':plan,'candidate':candidate,'evidence':evidence,'criteria':criteria,'confirmed':confirmed})
        return self._evaluate(goal,plan,candidate,evidence,criteria,confirmed)

    def _evaluate(self,goal,plan,candidate,evidence,criteria=None,confirmed=False):
        criteria=criteria if criteria is not None else plan['criteria']
        # 舊版企劃的紀錄仍可供回顧，但不直接充當新版企劃的通過證據。
        evidence=[row for row in evidence if row['plan_revision']==candidate['plan_revision']]
        output=candidate['text']; checks=[]; semantic=[]
        for criterion in criteria:
            verifier=criterion['verifier']; detail=criterion.get('check',{}); refs=[]
            if verifier=='independent_review': semantic.append(criterion); continue
            if verifier=='user': passed=confirmed; reason='使用者已確認此項完成條件。' if passed else '此項完成條件仍需使用者確認。'
            elif verifier=='output':
                passed=compare(output,detail); refs=[candidate['id']]; reason='已核對實際候選成果。'
            else:
                matching=[row for row in evidence if row['action']==detail['action'] and row['step_id']==detail['step_id']][-1:]
                refs=[row['id'] for row in matching if compare(field(row['result'],detail['path']),detail)]
                passed=bool(refs); reason='已核對平台保存的實際執行紀錄。' if passed else '缺少符合條件的實際執行證據。'
            checks.append({'id':criterion['id'],'passed':passed,'reason':reason,'evidence_ids':refs,'candidate_id':candidate['id']})
        if semantic:
            # 只傳原始目標、契約、成果與平台證據，不帶入執行者的推理訊息。
            rows=[{key:row[key] for key in ('id','step_id','action','digest','result','created_at','plan_revision')} for row in evidence]
            review=self.ask('acceptance',ACCEPTANCE_PROMPT,{'original_goal':goal,'plan':plan,'criteria':semantic,'candidate':candidate,'platform_evidence':rows,'checked_at':now()})
            values=review.get('checks') if isinstance(review,dict) else None
            expected={criterion['id'] for criterion in semantic}
            if not isinstance(values,list) or len(values)!=len(expected) or any(not isinstance(row,dict) for row in values) or {row.get('id') for row in values}!=expected:
                raise DomainError('獨立驗收未逐條回傳所有完成條件')
            by_id={row['id']:row for row in values}; allowed={row['id'] for row in evidence}
            for criterion in semantic:
                row=by_id[criterion['id']]; refs=row.get('evidence_ids')
                if type(row.get('passed')) is not bool or not isinstance(row.get('reason'),str) or not row['reason'].strip() or not isinstance(refs,list) or any(not isinstance(ref,str) for ref in refs):
                    raise DomainError('獨立驗收結果格式無效')
                valid_refs=allowed|({candidate['id']} if criterion['check']['scope']=='output' else set())
                passed=row['passed'] and bool(refs) and set(refs)<=valid_refs
                checks.append({'id':row['id'],'passed':passed,'reason':row['reason'] if passed or not row['passed'] else '驗收引用了不存在或不適用的證據。','evidence_ids':refs,'candidate_id':candidate['id']})
        return checks
