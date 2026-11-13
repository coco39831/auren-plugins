"""每個 Agent 的 MCP 插件目錄；自修改品質、使用者審批及操作授權分別校驗。"""
from dataclasses import asdict
from hashlib import sha256
import json
import re
import time
import threading
from copy import deepcopy
from pathlib import Path
from auren_contracts.capabilities import CapabilityV1,validate_json,validate_schema
from auren_contracts.models import ActionRequest,Denied,DomainError,NotReady,Source,canonical,uid
from auren_infra.plugins import PluginExecutor,scan_plugin,PLUGIN_RUNNER
from auren_infra.web_mcp import TOOLS


class Plugins:
    def __init__(self,runtime):self.r=runtime
    def require(self):
        self.r.require_activated();self.r.license.require_feature('extensions');self.r.license.require_feature('tools')
    def manifest(self):
        self._migrate_legacy()
        host=self.r.plugin_host;state=host.state();output={}
        for identifier,row in host.rows(state).items():
            if identifier in state['disabled']:continue
            for export in row['manifest']['exports']:
                if export['kind']!='tool' or host.bindings(state).get(export['contract'])!=identifier:continue
                path=Path(row['path']);host.manifest(row)
                legacy=path/'legacy-capability.json'
                if legacy.is_file():cap=json.loads(legacy.read_text(encoding='utf-8'))
                else:
                    cap={'id':identifier+'/'+export['id'],'name':identifier+'.'+export['id'],'description':row['manifest']['description'],
                        'signature':{key:json.loads((path/export[field]).read_text(encoding='utf-8')) for key,field in [('input','input_schema'),('output','output_schema')]},'preconditions':[],'version':1}
                output[cap['id']]={**cap,'package_id':identifier,'contract':export['contract'],'legacy_wrapped':legacy.is_file()}
        return output

    def _legacy_manifest(self):
        self.require();path=self.r.selfmod._path('skills')/'capabilities.json'
        if not path.exists():return {}
        return json.loads(path.read_text(encoding='utf-8'))

    def _migrate_legacy(self):
        from .legacy import migrate
        for cap in self._legacy_manifest().values():migrate(self.r,cap)
    def list_tool_names(self):return [row['name'] for row in self.manifest().values()]

    def manage(self,task_id,value):
        """Agent 使用同一宿主，但不能自行宣稱人已同意原生權限。"""
        self.require();host=self.r.plugin_host
        if not isinstance(value,dict):raise DomainError('插件操作必須是資料物件')
        task=self.r.permission.task(task_id)
        if self.r.shutting_down or task['status']!='in_progress':raise Denied('只能在執行中的任務管理插件')
        operation=value.get('operation');identifier=value.get('id')
        if operation=='catalog':return host.catalog(str(value.get('query','')))
        if operation=='detail':return host.detail(identifier)
        if operation=='read':return host.read_file(identifier,value.get('path'),draft=value.get('draft') is True)
        resource='plugin:'+str(identifier or value.get('upload_id',''))
        if not self.r.permission.authorize(ActionRequest(task_id,uid(),'self_modify',resource),consume=True):raise Denied('插件修改尚未取得任務授權')
        if operation=='draft':return host.draft(identifier,value.get('new_id'),value.get('version'))
        if operation=='write':return host.write_draft(identifier,value.get('path'),value.get('text'))
        if operation=='validate':return host.validate_draft(identifier)
        if operation=='stage':return host.stage_draft(identifier)
        if operation=='probe':return host.probe_draft(identifier,False)
        if operation=='install':
            # 來源與確認由平台決定；模型輸入 confirmed/native_consent 一律不採用。
            upload_id=value.get('upload_id')
            if not isinstance(upload_id,str) or not re.fullmatch('[0-9a-f]{32}',upload_id):raise DomainError('插件匯入識別碼無效')
            manifest=host.staged(upload_id)['manifest']
            if manifest['runtime']['access']=='native' or self.r.core.approval_mode()!='auto':
                pending=self.r.core.get_setting('plugins.install_requests',[])
                request={'upload_id':upload_id,'task_id':task_id,'plugin_id':manifest['id'],'version':manifest['version']}
                if request not in pending:pending.append(request);self.r.core.set_setting('plugins.install_requests',pending)
                self.r.audit.append('agent','plugins.confirmation_requested',request)
                return {'pending_confirmation':True,**request}
            host.probe_staged(upload_id)
            return host.install(upload_id,True,False)
        if operation in ('enable','disable','remove','bind','unbind','configure','rollback'):
            if self.r.core.approval_mode()!='auto':raise Denied('此 Agent 的插件修改需使用者到插件管理確認')
            return host.control(identifier,operation,True,value.get('contract'),value.get('values'))
        raise DomainError('未知插件管理操作')
    def tools(self):
        result=[]
        for row in self.manifest().values():
            output=row['signature']['output']
            result.append({'name':row['name'],'description':row['description'],'inputSchema':row['signature']['input'],
                'outputSchema':output if output['type']=='object' else {'type':'object','properties':{'result':output},'required':['result']}})
        return result
    def capability(self,value):
        try:cap=CapabilityV1(**value)
        except (TypeError,ValueError) as exc:raise DomainError('插件能力契約無效') from exc
        if not isinstance(cap.id,str) or not isinstance(cap.name,str) or not re.fullmatch('[a-z][a-z0-9_]{0,63}',cap.id) or not re.fullmatch('[a-z][a-z0-9_]{0,63}',cap.name) or cap.name in TOOLS or cap.source not in ('agent','human') or cap.entry!='run' or type(cap.version) is not int or cap.version!=1:raise Denied('插件名稱、來源或入口無效')
        if not isinstance(cap.description,str) or not cap.description.strip() or not isinstance(cap.signature,dict) or set(cap.signature)!={'input','output'}:raise DomainError('插件缺少能力說明或輸入輸出契約')
        if not isinstance(cap.preconditions,(list,tuple)) or any(not isinstance(item,str) or item.startswith('plugin:') for item in cap.preconditions):raise Denied('插件平台能力不能遞迴呼叫插件')
        for schema in cap.signature.values():validate_schema(schema)
        if cap.signature['input']['type']!='object':raise DomainError('插件資料不符合宣告型別')
        return asdict(cap)
    def propose(self,capability,code,reason,task_id):
        self.require();cap=self.capability(capability);scan_plugin(code)
        if self.r.permission.task(task_id)['status'] not in ('in_progress','blocked'):raise Denied('插件只能在執行中的任務呼叫')
        if not isinstance(code,str) or len(code.encode())>256*1024:raise DomainError('元件程式碼過大或格式無效')
        manifest=self._legacy_manifest()
        if any(row['name']==cap['name'] and key!=cap['id'] for key,row in manifest.items()):raise Denied('插件名稱已被使用')
        filename='plugin_'+cap['id']+'.py';cap.update(path=filename,hash=sha256(code.encode()).hexdigest())
        manifest[cap['id']]=cap;directory=self.r.selfmod._path('skills');changes=[]
        for name,content in ((filename,code),('capabilities.json',canonical(manifest))):
            path=directory/name
            changes.append({'path':name,'before':path.read_text(encoding='utf-8') if path.exists() else '',
                'after':content,'language':'python' if name.endswith('.py') else 'json'})
        identifier=self.r.selfmod.propose('skills',changes,reason)
        self.r.core.set_setting('plugin.proposal.'+identifier,{'task_id':task_id,'operation_id':uid(),'plugin_id':cap['id']})
        return {'proposal_id':identifier}
    def merge(self,identifier,task_id):
        self.require();saved=self.r.core.get_setting('plugin.proposal.'+identifier)
        if not saved or saved['task_id']!=task_id:raise Denied('插件提案不屬於此任務')
        result=self.r.selfmod.merge(identifier,ActionRequest(task_id,saved['operation_id'],'self_modify','component:skills'),self.r.core.approval_mode() or 'user')
        self._migrate_legacy()
        self.r.audit.append('agent','plugin.registered',{'plugin_id':saved['plugin_id'],'proposal_id':identifier})
        return result
    def invoke(self,name,arguments,task_id):
        self.require()
        if self.r.shutting_down or self.r.permission.task(task_id)['status']!='in_progress':raise Denied('插件只能在執行中的任務呼叫')
        cap=next((row for row in self.manifest().values() if row['name']==name or row['id']==name),None)
        if not cap:raise Denied('未知或尚未核准的插件')
        if not self.r.permission.authorize(ActionRequest(task_id,uid(),'execute_tool','plugin:'+cap['id']),consume=True):raise Denied('插件執行尚未取得授權')
        value=self.r.plugin_host.invoke(cap['contract'],arguments,{'agent_id':self.r.agent_id,'task_id':task_id,'run_id':uid()})
        if cap['legacy_wrapped']:
            if not isinstance(value,dict) or len(value)!=1:raise DomainError('舊能力轉接回應格式無效')
            if 'platform_request' in value:
                request=value['platform_request']
                if request['tool_id'] not in cap['preconditions']:raise Denied('插件未宣告此平台能力')
                value=self.platform_call(task_id,request['tool_id'],request['arguments'])
            else:value=value['result']
        validate_json(value,cap['signature']['output'])
        return value
    def platform_call(self,task_id,identifier,arguments):
        if not isinstance(arguments,dict):raise DomainError('插件平台請求格式無效')
        if identifier in ('web_search','web_read'):
            permitted={'query'} if identifier=='web_search' else {'url','render','wait_selector','steps'}
            if set(arguments)-permitted:raise Denied('插件平台請求格式無效')
            stop=getattr(self.r.task_workflow.active,'stop',None) or threading.Event()
            result=self.r.roaming.task_web(task_id,identifier,arguments.get('query') if identifier=='web_search' else arguments.get('url'),stop,
                arguments.get('render','auto'),arguments.get('wait_selector'),arguments.get('steps'))
            if self.r.task_workflow.state(task_id):self.r.task_workflow.record(task_id,identifier,result,{'action':identifier,**arguments})
            return result
        return self.r.execute_tool(task_id,identifier,arguments)

    def configure(self,identifier,values,task_id,operation_id,actor):
        self.require()
        if actor.source!=Source.HUMAN or identifier not in self.manifest() or not isinstance(values,dict) or len(canonical(values).encode())>32768:raise Denied('插件設定必須由使用者在授權範圍內提供資料物件')
        if not self.r.permission.authorize(ActionRequest(task_id,operation_id,'configure','plugin:'+identifier+':config'),consume=True):raise Denied('插件設定尚未取得授權')
        self.r.core.set_setting('plugin.configuration.'+identifier,self.r.vault.pack(values))
        self.r.audit.append(actor.subject,'plugin.configuration',{'plugin_id':identifier,'task_id':task_id})
        return {'done':True}

    def ui_data(self,identifier,what,task_id,request_id):
        self.require()
        cap=self.manifest().get(identifier)
        if not cap or what not in ('task','emotion','memory','config'):raise Denied('插件資料請求不在允許清單')
        if not isinstance(request_id,str) or not 1<=len(request_id)<=100:raise Denied('插件資料請求無效')
        task=self.r.permission.task(task_id)
        if what in ('memory','config'):
            action='read_memory' if what=='memory' else 'backend_service'
            if not self.r.permission.authorize(ActionRequest(task_id,request_id,action,'plugin:'+identifier+':'+what),consume=True):raise Denied('插件尚未取得這項資料授權')
        if what=='memory':return {'summary':self.r.memory.working()['text']}
        if what=='emotion':return self.r.views.emotion()
        if what=='config':
            saved=self.r.core.get_setting('plugin.configuration.'+identifier)
            return {**{key:cap[key] for key in ('id','name','description','version')},'configuration':self.r.vault.unpack(saved) if saved else {}}
        task=self.r.permission.task(task_id)
        return {key:task[key] for key in ('id','goal','progress_summary','status')}

    def evaluate(self,baseline,candidate):
        manifest=json.loads((candidate/'capabilities.json').read_text(encoding='utf-8'));cases=self.r.config.get('plugin_regression_cases',{})
        metrics={};details={}
        for side,directory in (('baseline',baseline),('candidate',candidate)):
            present=json.loads((directory/'capabilities.json').read_text(encoding='utf-8')) if (directory/'capabilities.json').exists() else {}
            correct=0;total=0;elapsed=[];safe=True
            for identifier,cap in manifest.items():
                samples=cases.get(identifier,[])
                if not samples:raise NotReady('插件缺少平台持有的回歸案例')
                for sample in samples:
                    total+=1
                    if identifier not in present:continue
                    source=(directory/present[identifier]['path']).read_text(encoding='utf-8');scan_plugin(source)
                    def broker(tool_id,arguments):
                        match=next((row for row in sample.get('platform_results',[]) if row['tool_id']==tool_id and canonical(row['arguments'])==canonical(arguments)),None)
                        if match is None:raise Denied('插件回歸缺少相同平台請求的保存結果')
                        return deepcopy(match['result'])
                    started=time.perf_counter()
                    try:
                        value=PluginExecutor(self.r.components.sandbox,self.r.components.gate).invoke(present[identifier],source,present[identifier]['hash'],sample['arguments'],broker)
                        valid=canonical(value)==canonical(sample['expected'])
                    except DomainError:valid=False;safe=False
                    elapsed.append(time.perf_counter()-started)
                    correct+=int(valid)
            metrics[side]={'behavior':correct/total,'performance':1/(1+sum(elapsed)/len(elapsed)) if elapsed else 0,'safety':int(safe)}
            details[side]={'cases':total,'correct':correct,'source':'platform:plugin_regression_cases'}
        return {**metrics,'plugin_regression':details,'safety_scope':'插件 AST 與隔離執行邊界；不是內容分類'}


class TaskPluginRegistry:
    def __init__(self,plugins,task_id):self.plugins,self.task_id=plugins,task_id
    def tools(self):return self.plugins.tools()
    def list_tool_names(self):return self.plugins.list_tool_names()
    def invoke(self,name,arguments):return self.plugins.invoke(name,arguments,self.task_id)
