"""所有 Agent 模型呼叫經可替換的上下文、思考與路由提供者。"""
from copy import deepcopy
from uuid import uuid4
from auren_contracts.models import Denied, NotReady
from auren_infra.model import ModelClient, is_external
from hashlib import sha256
from auren_contracts.models import canonical


class GatewayClient:
    def __init__(self, application, direct):
        object.__setattr__(self,'application',application)
        object.__setattr__(self,'direct',direct)

    def __getattr__(self,name): return getattr(self.direct,name)
    def __setattr__(self,name,value): setattr(self.direct,name,value)

    def complete(self,messages,schema=None):
        r=self.application; host=r.plugin_host
        context={'agent_id':r.agent_id,'run_id':str(uuid4())}
        active=getattr(r.task_workflow.active,'task_id',None)
        if active:context['task_id']=active
        stop=getattr(r.task_workflow.active,'stop',None)
        with host.snapshot(stopped=stop.is_set if stop else None):
            owner=r.default_model if getattr(r,'default_model',None) and r.core.get_setting('model_mode','inherit')=='inherit' else r
            available=owner.model_connections()
            prepared=host.invoke('auren.model.pipeline/1',{'messages':deepcopy(messages),'connections':available['connections'],'primary_model_id':available['primary_model_id']},context)
            receipts=host.receipts(context['run_id']);by_id={row['id']:row for row in receipts}
            def source(receipt,argument,contract,pointer):
                for link in receipt['consumed_receipts']:
                    row=by_id.get(link['id'])
                    if link['argument']==argument and link['pointer']==pointer and row and row['contract']==contract:return row
                raise Denied('模型 Skill 沒有實際使用必要提供者的輸出')
            assembled=next((row for row in reversed(receipts) if row['contract']=='auren.model.request/1' and row['output_sha256']==sha256(canonical(prepared).encode()).hexdigest()),None)
            if assembled is None:raise Denied('模型 Skill 缺少最終組裝的成功收據')
            thinking=source(assembled,'messages','auren.reasoning.prepare/1','/messages')
            source(thinking,'messages','auren.memory.context/1','/messages')
            route=source(assembled,'route','auren.model.route/1','')
            if source(route,'messages','auren.reasoning.prepare/1','/messages')['id']!=thinking['id']:raise Denied('模型路由與最終訊息沒有使用相同的思考結果')
            selected=prepared['route']
            client=self.direct
            if selected['connection_ref']!='primary':
                row=next((item for item in owner._model_connections() if item['id']==selected['connection_ref']),None)
                if row is None:raise Denied('路由插件選用了未設定的模型連線')
                config=deepcopy(row['config'])
                if is_external(config):
                    r.license.require_feature('external_model')
                    if row.get('external_consent') is not True:raise Denied('路由目標尚未取得外部模型資料傳輸同意')
                if config.get('provider')=='llama_embedded':config=r.managed_llama.connect(config)
                client=ModelClient(config,row.get('external_consent',False),r.vault.untext(row['key']) if row.get('key') else None,options=r.model_options(),diagnostics=r.diagnostics)
            result=client.complete(prepared['messages'],schema)
            r.audit.append('model.gateway','model.completed',{'run_id':context['run_id'],'task_id':active,'connection_ref':selected['connection_ref'],'required_receipts':[item['id'] for item in host.receipts(context['run_id'])]})
            return result
