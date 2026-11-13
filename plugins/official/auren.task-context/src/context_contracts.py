"""交接只驗證可查事實與資料形狀，主觀判斷仍保留模型來源。"""
from dataclasses import asdict
from auren_contracts.models import DomainError
from auren_contracts.task_context import HandoffV1


def string(value,empty=False):
    if not isinstance(value,str) or not empty and not value.strip(): raise DomainError('交接文字欄位無效')
    return value


def texts(value):
    if not isinstance(value,list) or any(not isinstance(item,str) for item in value): raise DomainError('交接清單欄位無效')
    return value


def rows(value,fields):
    if not isinstance(value,list): raise DomainError('交接清單欄位無效')
    for row in value:
        if not isinstance(row,dict) or any(key not in row for key in fields): raise DomainError('交接紀錄缺少必要欄位')
    return value


def handoff(value,step,result,evidence,used_ids,platform):
    if not isinstance(value,dict): raise DomainError('交接說明書必須是資料物件')
    allowed={row['id'] for row in evidence}
    completed=rows(value.get('completed'),('item','evidence_id'))
    for row in completed:
        string(row['item'])
        string(row['evidence_id'])
        if row['evidence_id'] not in allowed: raise DomainError('交接引用了未執行的證據')
    decisions=rows(value.get('decisions'),('decision','reason','alternatives','rejected_why'))
    for row in decisions:
        string(row['decision']);string(row['reason']);texts(row['alternatives']);string(row['rejected_why'],True)
    unresolved=rows(value.get('unresolved'),('issue','impact','attempted'))
    for row in unresolved: string(row['issue']);string(row['impact']);texts(row['attempted'])
    dead=rows(value.get('dead_ends'),('method','why_failed','contradicts_knowledge_id'))
    for row in dead:
        string(row['method']);string(row['why_failed'])
        if row['contradicts_knowledge_id'] is not None and row['contradicts_knowledge_id'] not in used_ids: raise DomainError('知識衝突未對應實際引用的知識')
    refs=texts(value.get('knowledge_used_ids'))
    if set(refs)!=set(used_ids): raise DomainError('交接知識引用與實際紀錄不符')
    diagnosis=value.get('flow_diagnosis')
    if result!='completed':
        if not dead or not isinstance(diagnosis,dict) or diagnosis.get('inference') not in ('flow_error','method_error','external'): raise DomainError('失敗交接缺少流程診斷')
        for key in ('failed_step','expected','actual'): string(diagnosis.get(key))
        refs_d=texts(diagnosis.get('evidence_ids'))
        if not refs_d or not set(refs_d)<=allowed: raise DomainError('失敗診斷缺少可查證據')
    elif diagnosis is not None: raise DomainError('成功交接不可偽裝為失敗診斷')
    return asdict(HandoffV1(step['id'],result,tuple(completed),tuple(decisions),tuple(unresolved),tuple(dead),
        string(value.get('handoff_notes'),True),string(value.get('agent_notes'),True),tuple(used_ids),diagnosis,platform))
