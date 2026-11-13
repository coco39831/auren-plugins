"""保存人物回覆當時的立場與依據；追問沿用當時快照，不回填事後理由。"""
from copy import deepcopy
from hashlib import sha256
import json
from auren_contracts.models import DomainError,Denied,canonical
from auren_contracts.personality_reasoning import PersonalitySourceV1,reasoning
from .shaping_prompts import REASONING_REQUEST


class PortraitReasoning:
    def __init__(self,runtime):self.r=runtime

    def source(self,identifier):
        row=self.r.memory.read(identifier)
        return PersonalitySourceV1(row['id'],row['text'],row['metadata'].get('role','event'))

    def _save(self,key,value):self.r.core.set_setting(key,self.r.vault.text(canonical(value)))
    def _load(self,key):
        value=self.r.core.get_setting(key)
        return json.loads(self.r.vault.untext(value)) if value else None

    def snapshot(self,identifier):
        value=json.loads(self.r.memory.read(identifier)['text'])
        if not isinstance(value,dict) or value.get('format')!='portrait_reasoning_v1' or sha256(value['output'].encode()).hexdigest()!=value['output_sha256']:raise Denied('人物回覆依據快照不符')
        return value

    def previous(self,conversation_id):
        if not conversation_id:return []
        item=next((row for row in self.r.conversations() if row['id']==conversation_id),None)
        if not item:return []
        result=[];seen=set()
        for task_id in reversed(item['task_ids']):
            delivered={sha256(row['content'].encode()).hexdigest() for row in self.r.memory.task_messages([task_id]) if row['role']=='assistant'}
            for identifier in reversed(self._load('personality.reasoning.responses.'+task_id) or []):
                saved=self.snapshot(identifier)
                if saved['output_sha256'] in delivered:
                    root=saved.get('target_response_id') or identifier
                    if root not in seen:
                        seen.add(root);result.append({'response_id':root,'text':saved['output']})
                        if len(result)==8:return list(reversed(result))
        return list(reversed(result))

    def prepare(self,task_id,goal,chat=None):
        key='personality.reasoning.request.'+task_id
        if self._load(key) is not None:return
        portrait=self.r.emotion.get_setting('persona',{}).get('portrait',{})
        current=deepcopy(portrait.get('reasoning') or {'version':1,'beliefs':[]})
        previous=self.previous((chat or {}).get('conversation_id'))
        value={'is_explanation':False,'response_id':None,'belief_ids':[]}
        if previous or current['beliefs']:
            value=self.r.personality_dialogue.ask(REASONING_REQUEST,{'message':goal,'previous_responses':previous,'beliefs':current['beliefs']})
            if type(value.get('is_explanation')) is not bool or not isinstance(value.get('belief_ids'),list) or any(not isinstance(item,str) for item in value['belief_ids']):raise DomainError('人物思路追問判定格式無效')
        target=value.get('response_id');allowed={row['response_id'] for row in previous}
        if target is not None and target not in allowed:raise Denied('人物追問不對應此對話的回覆')
        if not value['is_explanation'] and target is not None:raise DomainError('人物思路追問判定格式無效')
        saved=self.snapshot(target) if target else None
        if saved:
            current=deepcopy(saved['reasoning']);used=list(saved['used_belief_ids']);style=deepcopy(saved['reasoning_style'])
        else:
            used=value['belief_ids'];style=deepcopy(portrait.get('reasoning_style',{}))
        if len(set(used))!=len(used) or not set(used)<={row['id'] for row in current['beliefs']}:raise Denied('人物回覆引用不存在的立場')
        self._save(key,{'is_explanation':value['is_explanation'],'target_response_id':target,'target_output':saved['output'] if saved else None,
            'reasoning':current,'reasoning_style':style,'used_belief_ids':used,'question_sha256':sha256(goal.encode()).hexdigest()})

    def context(self,task_id):return self._load('personality.reasoning.request.'+task_id) or {}

    def _closure(self,beliefs,selected):
        by_id={row['id']:row for row in beliefs};seen=set();todo=list(selected)
        while todo:
            identifier=todo.pop()
            if identifier in seen:continue
            if identifier not in by_id:raise Denied('人物回覆引用不存在的立場')
            seen.add(identifier)
            for entry in by_id[identifier]['basis']:
                if entry['kind']=='inference':todo.extend(entry['from'])
        return [deepcopy(row) for row in beliefs if row['id'] in seen]

    def _explanation(self,beliefs):
        # 只組合已落盤的立場與依據；模型夾帶的新故事不混入「當時的理由」。
        lines=[]
        for row in beliefs:
            if not row['basis']:continue
            lines.append('「'+row['claim']+'」')
            for entry in row['basis']:
                lines.append('「'+entry.get('quote',entry.get('how',entry.get('what','')))+'」')
        return self.r.language.text('我當時留下的依據是：')+'\n'+'\n'.join(lines)

    def validate(self,task_id,decision):
        context=self.context(task_id)
        if not context:raise DomainError('人物回覆尚未保存當時的思路依據')
        output=decision.get('text','');support=decision.get('reasoning_support',{})
        if not isinstance(output,str) or not isinstance(support,dict):raise DomainError('人物回覆依據格式無效')
        selected=support.get('belief_ids',[])
        if not isinstance(selected,list) or any(not isinstance(item,str) for item in selected) or len(set(selected))!=len(selected):raise DomainError('人物回覆依據格式無效')
        if context['is_explanation']:
            if selected and not set(selected)<=set(context['used_belief_ids']):raise Denied('人物追問不能改用事後形成的立場')
            selected=context['used_belief_ids']
        beliefs=self._closure(context['reasoning']['beliefs'],selected)
        # 重新查核記憶來源，不讓遺失或錯置的索引冒充實際依據。
        reasoning({'version':1,'beliefs':beliefs},resolve=self.source)
        proof={'version':1,'beliefs':beliefs}
        cache_key=None
        if context['is_explanation']:
            cache_key='personality.reasoning.explanation.'+sha256(canonical({'target':context['target_response_id'] or context['question_sha256'],'reasoning':proof,'selected':selected,'language':self.r.language.code}).encode()).hexdigest()
            cached=self._load(cache_key)
            if cached:
                output=self.snapshot(cached['record_id'])['output']
            elif selected:
                by_id={row['id']:row for row in beliefs}
                for identifier in selected:
                    excerpts=[entry.get('quote',entry.get('how',entry.get('what',''))) for entry in by_id[identifier]['basis']]
                    if excerpts and not any(text and text in output for text in excerpts):raise Denied('人物追問回覆必須引用已記錄的依據')
                attempt=output if any(not by_id[identifier]['basis'] for identifier in selected) else None
                output=self._explanation(beliefs) if any(row['basis'] for row in beliefs) else ''
                # 未留下依據的嘗試，與原先保存的依據分開標示，不回填成歷史。
                if attempt:output+='\n'+self.r.language.text('這是我現在補充的想法，不是先前已記錄的理由。')+'\n'+attempt
            else:output=self.r.language.text('這是我現在補充的想法，不是先前已記錄的理由。')+'\n'+output
        return output,{'format':'portrait_reasoning_v1','task_id':task_id,'output':output,'output_sha256':sha256(output.encode()).hexdigest(),
            'reasoning':proof,'reasoning_style':context['reasoning_style'],'used_belief_ids':list(selected),'target_response_id':context['target_response_id'] if context['is_explanation'] else None,
            'explanation':context['is_explanation'],'cache_key':cache_key}

    def record(self,snapshot):
        task_id=snapshot['task_id'];signature=sha256(canonical(snapshot).encode()).hexdigest()
        identifier=self.r.memory.append(canonical(snapshot),'raw','event',{'kind':'portrait_reasoning','task_id':task_id},idempotency_key='portrait.reasoning:'+signature)
        key='personality.reasoning.responses.'+task_id;refs=self._load(key) or []
        if identifier not in refs:self._save(key,refs+[identifier])
        return identifier

    def delivered(self,identifier):
        value=self.snapshot(identifier)
        if value.get('cache_key'):self._save(value['cache_key'],{'record_id':identifier})
