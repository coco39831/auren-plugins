"""收件時即存在的任務企劃；觸發事實由平台記錄，模型只整理目標與做法。"""
from auren_contracts.models import DomainError
from auren_contracts.task_flow import TaskBriefV1,TaskTriggerV1
from .task_planning import text,strings
from .task_prompts import INTAKE_PROMPT


class TaskIntake:
    def __init__(self,ask,localize): self.ask=ask; self.localize=localize

    def draft(self,goal,trigger):
        return TaskBriefV1(goal[:80],goal,(goal,),tuple(self.localize(value) for value in (
            '確認這次委託的目標、預期成果與待釐清事項。',
            '查閱相關資訊，將處理方向拆成可執行的子任務。',
            '在已授權範圍內執行，逐條核對完成條件並取得必要確認。')),
            (),trigger)

    def context_information(self,context):
        records=[]
        for row in context.get('conversation_history',[]):
            if isinstance(row.get('id'),str) and isinstance(row.get('content'),str):
                records.append({'id':'message:'+row['id'],'text':row['content'],'kind':row.get('role'),'at':row.get('created_at')})
        for index,row in enumerate(context.get('human_feedback',[])):
            if isinstance(row.get('text'),str): records.append({'id':'feedback:'+str(index),'text':row['text'],'kind':'feedback','at':row.get('at')})
        return records

    def decode(self,value,brief,available_context=()):
        if not isinstance(value,dict): raise DomainError('初始任務企劃格式無效')
        objectives=strings(value.get('objectives'),'objectives',64)
        outline=strings(value.get('outline'),'outline',32)
        if not objectives or not outline: raise DomainError('初始任務企劃必須列明目標與大致流程')
        references=strings(value.get('context_ids',[]),'context_ids',64)
        known={row['id']:row for row in available_context}
        if not set(references)<=set(known): raise DomainError('初始任務企劃引用了未提供的背景資訊')
        trigger=TaskTriggerV1(**brief['trigger'])
        return TaskBriefV1(text(value.get('title'),'brief.title'),text(value.get('goal'),'brief.goal'),
            objectives,outline,strings(value.get('open_questions',[]),'open_questions',20),trigger,'outlined',brief['revision']+1,
            context_information=tuple(known[key] for key in dict.fromkeys(references)))

    def outline(self,goal,brief,context):
        corrections=[]
        for _ in range(3):
            proposed=self.ask('task_intake',INTAKE_PROMPT,{'original_goal':goal,'initial_brief':brief,**context,'corrections':corrections})
            try: return self.decode(proposed,brief,context.get('available_context',[]))
            except DomainError as exc: corrections=[self.localize(str(exc))]
        raise DomainError('初始任務企劃尚未形成有效目標與流程')
