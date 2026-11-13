"""六種流程均經版本化能力契約；必要提供者失敗即停止，不默默退回舊流程。"""
from copy import deepcopy
from uuid import uuid4
from auren_contracts.flow import hook, schemas
from auren_contracts.models import DomainError, canonical


class FlowHooks:
    def hooks(self):
        return [{**row,'replaceable':True,'capability':'auren.'+row['id']+'/1'} for row in schemas()]

    def invoke_flow(self,hook_id,original,payload,validate=None):
        definition=hook(hook_id)
        host=self.runtime.plugin_host; captured=[]
        def checked(result):
            if validate is not None:return validate(deepcopy(result))
            kind=definition.output_contract['type']
            if kind=='object' and not isinstance(result,dict) or kind=='array' and not isinstance(result,list) or kind=='null' and result is not None:raise DomainError('流程提供者回應不符合契約')
            if hook_id=='flow.acceptance':
                criteria=payload.get('criteria') or payload.get('plan',{}).get('criteria',[])
                expected={item['id'] for item in criteria}
                if any(not isinstance(item,dict) or type(item.get('passed')) is not bool or not isinstance(item.get('reason'),str) or not isinstance(item.get('evidence_ids'),list) for item in result) or len(result)!=len(expected) or {item.get('id') for item in result}!=expected:
                    raise DomainError('流程驗收未逐條回傳所有完成條件')
            return result
        def continuation(value):
            from .components import dto
            # 舊 policy 只作資料相容轉接；沿用原授權，錯誤同樣停止。
            arguments=({'version':1,'payload':deepcopy(payload)},)
            before=self._execute(hook_id,'flow',arguments,{},'before')
            if before['mode']=='replace':result=checked(before.get('value'))
            else:
                result=original()
                after=self._execute(hook_id,'flow',arguments,{},'after',result)
                if after['mode']=='replace':result=checked(after.get('value'))
            captured.append((result,dto(result)))
            return deepcopy(captured[-1][1])
        context={'agent_id':self.runtime.agent_id,'run_id':str(uuid4())}
        if payload.get('task_id'):context['task_id']=payload['task_id']
        with host.continuation('flow.original',continuation):
            result=host.invoke('auren.'+hook_id+'/1',{'version':1,'payload':deepcopy(payload)},context)
        # 延續原先已驗證的 DTO 時保留型別；不再重跑模型審查或增加 revision。
        if captured and canonical(result)==canonical(captured[-1][1]):return captured[-1][0]
        return checked(result)
