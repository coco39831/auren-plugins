"""每個 Agent 的元件啟用、人工委託與隔離程式碼覆寫。授權控制本身不交給候選程式。"""
from dataclasses import asdict,is_dataclass
from hashlib import sha256
import json
import threading
from auren_contracts.models import Actor,Source,Denied,DomainError,NotReady,ActionRequest,Change,GateDecision,canonical,uid,now
from auren_core.gate import IMMUTABLE,FLOW

NAMES={'audit':'稽核保障','constitution':'憲法規則','contracts':'服務協定','core':'核心調度','emotion_switches':'情感開關設定','gate':'安全閘門','infra':'隔離執行','license':'授權驗證','memory_base':'記憶讀寫底座','permission':'權限機制','rule_engine':'規則引擎'}
NAMES.update({'flow.intake':'任務收件','flow.plan':'任務企劃','flow.step_context':'子任務資訊','flow.knowledge_gate':'知識核對','flow.acceptance':'完成驗收','flow.replan':'方法重擬'})
DEFAULT_CODE='''def handle(request):
    # 沿用內建實作；可依 operation、stage、configuration 與 result 計算覆寫結果。
    return {"mode": "delegate"}
'''
METHODS={
 'audit':{'append'},'infra':{'run'},'license':{'check','require_feature'},
 'memory_base':{'append','read','search','working','write_working','archive','restore_hot','correct','list_records','page','export_records','task_messages'},
 'permission':{'authorize','parse','request_grant','grants','create_task','task','tasks','update_task'},
 'rule_engine':{'rules','propose_rule'},'emotion_switches':{'expression'},
}
RUNNER='''import json
from policy import handle
with open("request.json", encoding="utf-8") as source:
    request = json.load(source)
print(json.dumps(handle(request), ensure_ascii=False, allow_nan=False))
'''

def dto(value):
    if is_dataclass(value): return {key:dto(item) for key,item in asdict(value).items()}
    if isinstance(value,dict): return {str(key):dto(item) for key,item in value.items()}
    if isinstance(value,(list,tuple)): return [dto(item) for item in value]
    if value is None or isinstance(value,(str,int,float,bool)): return value
    raise DomainError('元件覆寫只接受版本化資料，不傳送內部物件')

class Proxy:
    def __init__(self,control,component,service,methods):
        self._control,self._component,self._service,self._methods=control,component,service,methods
    def __getattr__(self,name):
        original=getattr(self._service,name)
        if name not in self._methods or not callable(original): return original
        return lambda *args,**kwargs:self._control.invoke(self._component,name,original,args,kwargs)
    def __setattr__(self,name,value):
        if name.startswith('_'): object.__setattr__(self,name,value)
        else: setattr(self._service,name,value)

class AgentGate:
    def __init__(self,control,gate): self._control,self._gate=control,gate
    def __getattr__(self,name): return getattr(self._gate,name)
    def __setattr__(self,name,value):
        if name.startswith('_'): object.__setattr__(self,name,value)
        else: setattr(self._gate,name,value)
    def evaluate(self,actor,change):
        # 人工委託、路徑與原生掃描是載入邊界；覆寫版本不能偽造委託或擴大修改對象。
        decision=self._gate.evaluate(actor,change)
        if not decision.allowed: return decision
        result=self._control.invoke('constitution','evaluate',lambda *_:asdict(decision),(dto(actor),dto(change)),{})
        result=self._control.invoke('gate','evaluate',lambda *_:result,(dto(actor),dto(change)),{})
        if not isinstance(result,dict) or type(result.get('allowed')) is not bool: raise DomainError('閘門覆寫回應格式錯誤')
        return GateDecision(result['allowed'],tuple(result.get('reasons',[])),decision.digest)

class DelegatedPermission:
    def __init__(self,control,permission): self.control,self.permission=control,permission
    def __getattr__(self,name): return getattr(self.permission,name)
    def authorize(self,request,consume=False):
        if request.action=='self_modify' and request.resource.startswith('component:'):
            component=request.resource.split(':',1)[1]
            if component in IMMUTABLE:
                self.control.require_agent(component,'code')
                if consume: self.control.journal.append('agent','component.delegation_used',{'component':component,'task_id':request.task_id,'operation_id':request.operation_id})
                return True
        return self.permission.authorize(request,consume)

from .flow_hooks import FlowHooks

class Components(FlowHooks):
    def __init__(self,runtime):
        self.runtime=runtime; self.store=runtime.core; self.vault=runtime.vault; self.lock=threading.RLock()
        self.journal=runtime.audit; self.gate=runtime.gate; self.sandbox=runtime.infra; self.license=runtime.license
        self.originals={name:getattr(runtime,name) for name in ('audit','gate','infra','license','memory','permission','emotion')}
        runtime.audit=Proxy(self,'audit',runtime.audit,METHODS['audit'])
        runtime.infra=Proxy(self,'infra',runtime.infra,METHODS['infra'])
        runtime.license=Proxy(self,'license',runtime.license,METHODS['license'])
        runtime.memory=Proxy(self,'memory_base',runtime.memory,METHODS['memory_base'])
        runtime.permission=Proxy(self,'rule_engine',Proxy(self,'permission',runtime.permission,METHODS['permission']),METHODS['rule_engine'])
        runtime.emotion=Proxy(self,'emotion_switches',runtime.emotion,METHODS['emotion_switches'])
        runtime.gate=AgentGate(self,runtime.gate)
        runtime.selfmod.gate=runtime.gate
        runtime.selfmod.permission=DelegatedPermission(self,runtime.permission)
        self.originals['emotion'].authorize_delegation=lambda actor:self.require_agent('emotion_switches','settings')
        # 回歸評估使用原始隔離服務與原生檢查，不採信候選版本自報的分數。

    def detach(self):
        for name,original in self.originals.items(): setattr(self.runtime,name,original)

    def enabled(self,component):
        return self.store.get_setting('component.enabled.'+component,True)

    def require_human(self,actor):
        if actor.source!=Source.HUMAN: raise Denied('只有使用者可更改元件委託開關')
        if not self.store.get_setting('developer_mode',False): raise Denied('請先在本地開啟進階修改模式')
        self.runtime._verify_developer()

    def require_agent(self,component,kind):
        if component in FLOW and kind=='code': return
        if component not in IMMUTABLE or kind not in ('settings','code'): raise DomainError('元件或委託範圍無效')
        if not self.store.get_setting('developer_mode',False): raise Denied('本地進階修改模式尚未啟用')
        if not self.store.get_setting('agent_modify.'+component+'.'+kind,False): raise Denied('尚未授權 Agent 修改此元件的'+('設定' if kind=='settings' else '程式碼'))
        self.runtime._verify_developer()

    def delegation(self,component,kind,enabled,actor,confirmed=False):
        if component not in IMMUTABLE or kind not in ('settings','code') or type(enabled) is not bool: raise DomainError('元件或授權選項無效')
        if actor.source!=Source.HUMAN: raise Denied('Agent 不能自行取得或擴大元件修改授權')
        if enabled:
            self.require_human(actor)
            if confirmed is not True: raise Denied('請先確認這項元件修改委託的風險')
        with self.lock:
            self.store.set_setting('agent_modify.'+component+'.'+kind,enabled)
            self.journal.append(actor.subject,'component.delegation',{'agent_id':self.runtime.agent_id,'component':component,'kind':kind,'enabled':enabled,'source':actor.source,'at':now()})
        return self.panel()

    def configure(self,component,values,actor):
        if component not in IMMUTABLE or not isinstance(values,dict) or not values: raise DomainError('元件設定無效')
        if 'configuration' in values or 'max_steps' in values:
            raise Denied('框架參數只由成長機制調整')
        if set(values)-{'enabled','configuration','max_steps','performance','reminder','compute','trauma'}: raise Denied('此介面不能修改授權、簽章金鑰或元件修改委託')
        if actor.source==Source.AGENT: self.require_agent(component,'settings')
        elif actor.source==Source.HUMAN: self.require_human(actor)
        else: raise Denied('設定來源無效')
        if 'configuration' in values and (not isinstance(values['configuration'],dict) or len(canonical(values['configuration']).encode())>32768): raise DomainError('元件設定需為不超過 32 KiB 的資料物件')
        for name in ('enabled','performance','reminder','compute','trauma'):
            if name in values and type(values[name]) is not bool: raise DomainError('元件開關必須是明確的開或關')
        if 'max_steps' in values and (component!='core' or type(values['max_steps']) is not int): raise DomainError('推理步數必須是整數；建議區間為 1 到 10000，極端值可能使任務停止')
        if any(name in values for name in ('performance','reminder','compute','trauma')) and component!='emotion_switches': raise DomainError('此設定只屬於情感開關元件')
        with self.lock:
            if 'compute' in values and actor.source!=Source.HUMAN: raise Denied('情緒流動只接受本地使用者調整')
            if 'enabled' in values:
                self.store.set_setting('component.enabled.'+component,values['enabled'])
            if 'configuration' in values: self.store.set_setting('component.configuration.'+component,self.vault.pack(values['configuration']))
            if 'max_steps' in values: self.store.configure_engine({'max_steps':values['max_steps']},self.store.get_setting('engine.revision',0))
            for name in ('performance','reminder','trauma'):
                if name in values: self.originals['emotion'].set_switch(name,values[name],actor)
            if 'compute' in values:
                self.originals['emotion'].set_switch('compute',values['compute'],actor)
            self.journal.append(actor.subject,'component.settings',{'agent_id':self.runtime.agent_id,'component':component,'fields':sorted(values),'source':actor.source})
        return {'done':True}

    def configuration(self,component):
        value=self.store.get_setting('component.configuration.'+component)
        return self.vault.unpack(value) if value else {}

    def panel(self):
        master=self.store.get_setting('developer_mode',False); rows=[]
        for name in sorted(IMMUTABLE | FLOW):
            enabled=self.enabled(name); code=self.store.get_setting('agent_modify.'+name+'.code',False); version=self.active(name)
            rows.append({'id':name,'category':'flow' if name in FLOW else 'immutable','name':NAMES.get(name,name),'enabled':enabled,'agent_settings':self.store.get_setting('agent_modify.'+name+'.settings',False),'agent_code':code,'human_edit':self.store.get_setting('switch.'+name,False),'configuration':self.configuration(name),'version':version,'using_override':bool(enabled and version and (name in FLOW or master and code))})
        return {'enabled':master,'components':rows,'boundary':'元件啟用與修改授權分開。委託開關只能由使用者操作；程式碼在每個 Agent 的隔離環境執行。'}

    def active(self,component):
        value=self.store.get_setting('component.version.'+component)
        return value

    def source(self,component):
        if component not in IMMUTABLE | FLOW: raise DomainError('未知元件')
        active=self.active(component)
        path=self.runtime.selfmod._path(component)/'policy.py'
        operations=sorted(METHODS.get(component,{'decision'} if component in ('core','contracts') else {'evaluate'}))
        return {'component':component,'code':path.read_text(encoding='utf-8') if active and path.is_file() else DEFAULT_CODE,'version':active,'operations':operations,'configuration':self.configuration(component),'protocol':{'version':1,'request':['component','operation','stage','arguments','keyword_arguments','configuration','result'],'stage':['before','after'],'responses':{'delegate':{'mode':'delegate'},'replace':{'mode':'replace','value':'需符合原 Service 回傳契約的資料'}},'boundary':'只處理此 Agent 的資料傳輸物件，不直接匯入主機內部物件或修改其他 Agent。'}}

    def _execute(self,component,operation,args,kwargs,stage,result=None):
        active=self.active(component)
        if not active or component not in FLOW and not self.store.get_setting('agent_modify.'+component+'.code',False): return {'mode':'delegate'}
        self.require_agent(component,'code')
        path=self.runtime.selfmod._path(component)/'policy.py'
        code=path.read_text(encoding='utf-8')
        if sha256(code.encode()).hexdigest()!=active['hash']: raise Denied('元件覆寫版本完整性不符，請回滾')
        request={'version':1,'component':component,'operation':operation,'stage':stage,'arguments':dto(args),'keyword_arguments':dto(kwargs),'configuration':self.configuration(component),'result':dto(result)}
        if len(canonical(request).encode())>1024*1024: raise Denied('元件呼叫資料超過隔離通道上限')
        verdict=self.sandbox.run({'policy.py':code,'request.json':canonical(request),'invoke.py':RUNNER},['python3','invoke.py'],30)
        if verdict['returncode']!=0: raise DomainError('元件覆寫執行失敗，原始資料保留；可回滾至內建版本')
        try: response=json.loads(verdict['stdout'])
        except (ValueError,TypeError) as exc: raise DomainError('元件覆寫未回傳有效資料') from exc
        if not isinstance(response,dict) or response.get('mode') not in ('delegate','replace'): raise DomainError('元件回應模式無效')
        return response

    def _fallback(self,component,error):
        active=self.active(component)
        self.store.set_setting('component.version.'+component,None)
        if active:
            try: self.runtime.versions.restore(active['rollback_version'],self.runtime.selfmod._path(component))
            except (DomainError,OSError,KeyError) as restore_error:
                # 無法還原檔案也保持停用覆寫，避免再次載入損壞版本。
                self.journal.append('system','component.snapshot_restore_failed',{'component':component,'error':str(restore_error)[:500]})
        self.store.set_setting('component.error.'+component,str(error)[:500])
        self.journal.append('system','component.automatic_rollback',{'component':component,'error':str(error)[:500]})

    def _result(self,component,operation,response):
        value=response.get('value')
        if operation=='evaluate' and (not isinstance(value,dict) or type(value.get('allowed')) is not bool or not isinstance(value.get('reasons',[]),list) or any(not isinstance(reason,str) for reason in value.get('reasons',[]))): raise DomainError('閘門覆寫結果不符合契約')
        if operation=='authorize' and type(value) is not bool: raise DomainError('權限覆寫結果不符合契約')
        if operation=='decision' and (not isinstance(value,dict) or value.get('action') not in ('respond','step_complete','context_read','clarify','memory','knowledge','knowledge_learn','task_search','task_history','web_search','web_read','web_roam','tool','permission','system_info','system_settings','component_info','component_settings','component_code','component_probe','component_merge','plugin_list','plugin_propose','plugin_manage')): raise DomainError('調度覆寫結果不符合契約')
        if component=='audit' and (not isinstance(value,str) or len(value)!=64): raise DomainError('稽核覆寫結果不符合契約')
        if component=='infra' and (not isinstance(value,dict) or type(value.get('returncode')) is not int or not isinstance(value.get('stdout'),str) or not isinstance(value.get('stderr'),str)): raise DomainError('隔離覆寫結果不符合契約')
        if component=='emotion_switches' and (not isinstance(value,dict) or type(value.get('enabled')) is not bool): raise DomainError('情感覆寫結果不符合契約')
        if operation in ('working','write_working') and (not isinstance(value,dict) or not isinstance(value.get('text'),str) or type(value.get('version')) is not int): raise DomainError('工作記憶覆寫結果不符合契約')
        if component=='memory_base':
            if operation in ('append','correct') and not isinstance(value,str): raise DomainError('記憶識別碼覆寫結果不符合契約')
            if operation=='read' and (not isinstance(value,dict) or not isinstance(value.get('id'),str) or not isinstance(value.get('text'),str) or not isinstance(value.get('metadata'),dict)): raise DomainError('記憶覆寫結果不符合契約')
            if operation in ('search','list_records','export_records','task_messages') and not isinstance(value,list): raise DomainError('記憶清單覆寫結果不符合契約')
            if operation=='page' and (not isinstance(value,dict) or not isinstance(value.get('entries'),list) or type(value.get('total')) is not int): raise DomainError('記憶分頁覆寫結果不符合契約')
        return value

    def invoke(self,component,operation,original,args,kwargs):
        if not self.enabled(component):
            if component=='audit': return '0'*64
            if component in ('gate','constitution'): return original(*args,**kwargs)
            raise Denied(NAMES[component]+'已由使用者或獲授權的 Agent 暫停')
        try:
            before=self._execute(component,operation,args,kwargs,'before')
            if before['mode']=='replace': return self._result(component,operation,before)
        except (DomainError,OSError,ValueError) as exc:
            self._fallback(component,exc)
            return original(*args,**kwargs)
        result=original(*args,**kwargs)
        try:
            after=self._execute(component,operation,args,kwargs,'after',result)
            return self._result(component,operation,after) if after['mode']=='replace' else result
        except (DomainError,OSError,ValueError) as exc:
            self._fallback(component,exc)
            return result

    def decision(self,value):
        for component in ('core','contracts'):
            value=self.invoke(component,'decision',lambda value:value,(value,),{})
        if not isinstance(value,dict): raise DomainError('元件覆寫的下一步不是資料物件')
        return value

    def propose(self,component,code,reason,task_id):
        self.require_agent(component,'code')
        if not isinstance(code,str) or len(code.encode())>256*1024: raise DomainError('元件程式碼過大或格式無效')
        import ast
        try: tree=ast.parse(code)
        except SyntaxError as exc: raise DomainError('元件程式碼語法錯誤') from exc
        if not any(isinstance(node,ast.FunctionDef) and node.name=='handle' and len(node.args.args)==1 for node in tree.body): raise DomainError('元件程式需提供 handle(request) 入口')
        directory=self.runtime.selfmod._path(component); path=directory/'policy.py'
        if not self.active(component):
            if path.is_file() and path.read_text(encoding='utf-8')!=DEFAULT_CODE:
                self.runtime.versions.snapshot(component,directory,'保留未啟用的覆寫程式，回歸基準改用內建委派')
            path.write_text(DEFAULT_CODE,encoding='utf-8')
        identifier=self.runtime.selfmod.propose(component,[{'path':'policy.py','before':path.read_text(encoding='utf-8'),'after':code,'language':'python'}],reason)
        self.store.set_setting('component.proposal.'+identifier,{'component':component,'task_id':task_id,'operation_id':uid(),'hash':sha256(code.encode()).hexdigest()})
        return {'proposal_id':identifier}

    def merge(self,identifier):
        record=self.store.get_setting('component.proposal.'+identifier)
        if not record: raise Denied('此提案不屬於元件覆寫流程')
        self.require_agent(record['component'],'code')
        request=ActionRequest(record['task_id'],record['operation_id'],'self_modify','component:'+record['component'])
        result=self.runtime.selfmod.merge(identifier,request,self.store.approval_mode() or 'user')
        self.store.set_setting('component.version.'+record['component'],{'proposal_id':identifier,'hash':record['hash'],'rollback_version':result['rollback_version'],'at':now()})
        self.journal.append('agent','component.version_activated',{'component':record['component'],'proposal_id':identifier})
        return result

    def rollback(self,component,actor):
        if actor.source!=Source.HUMAN: raise Denied('回復內建元件須由使用者操作')
        if component not in IMMUTABLE | FLOW: raise DomainError('未知元件')
        self.store.set_setting('component.version.'+component,None)
        self.journal.append(actor.subject,'component.fallback',{'component':component})
        return {'done':True}
