"""將已核准舊能力無損封裝為 v1 套件；舊檔仍保留，執行只走新宿主。"""
from hashlib import sha256
import json
from pathlib import Path
import tempfile
import zipfile
from auren_contracts.models import Denied, NotReady, canonical
from auren_contracts.plugin_format import check
from auren_infra.plugins import scan_plugin

WORKER='''import json,sys
from logic import run
ready=False
for line in sys.stdin:
    request=json.loads(line); method=request['method']; params=request.get('params',{})
    if method=='plugin.initialize':ready=True;value={'protocol_version':1}
    elif not ready:raise ValueError('尚未初始化')
    elif method=='plugin.health':value={'status':'ready'}
    elif method=='plugin.shutdown':value={'status':'stopped'}
    elif method=='plugin.invoke':
        result=run(params['input'])
        value=result if isinstance(result,dict) and 'platform_request' in result else {'result':result}
    else:raise ValueError('未知方法')
    print(json.dumps({'jsonrpc':'2.0','id':request['id'],'result':value},ensure_ascii=False,allow_nan=False),flush=True)
    if method=='plugin.shutdown':break
'''


def migrate(runtime,cap):
    host=runtime.plugin_host;key='plugins.migrated.'+cap['id'];previous=runtime.core.get_setting(key,{})
    if previous.get('hash')==cap['hash']:return
    owner=runtime.selfmod._path('skills').resolve();source=owner/cap['path']
    if source.name!='plugin_'+cap['id']+'.py' or source.is_symlink() or not source.resolve().is_relative_to(owner):raise Denied('舊插件路徑無效')
    code=source.read_text(encoding='utf-8')
    if sha256(code.encode()).hexdigest()!=cap['hash']:raise Denied('舊插件完整性不符')
    scan_plugin(code)
    samples=runtime.config.get('plugin_regression_cases',{}).get(cap['id'],[])
    if not samples:raise NotReady('舊插件需補上原品質回歸案例才能轉為統一套件，未退回舊執行方式')
    identifier='auren.generated.'+cap['id'].replace('_','-')
    version='1.0.'+str(previous.get('revision',0)+1)
    contract='auren.tool.'+cap['id']+'/1'
    output={'type':'object','properties':{'result':cap['signature']['output'],'platform_request':{'type':'object','required':['tool_id','arguments'],'properties':{'tool_id':{'type':'string'},'arguments':{'type':'object'}},'additionalProperties':False}},'additionalProperties':False}
    manifest={'format_version':1,'id':identifier,'name':cap['name'],'version':version,'host_api':1,'description':cap['description'],'author':cap['source'],'license':'User-owned',
        'runtime':{'kind':'python','entrypoint':'src/main.py','platforms':['portable'],'access':'brokered','transport':'stdio'},
        'exports':[{'id':cap['id'],'kind':'tool','contract':contract,'input_schema':'schemas/input.json','output_schema':'schemas/output.json'}],
        'requires':[{'contract':'auren.executor.brokered/1','required':True}], 'dependencies':[],
        'permissions':([{'capability':'platform.tool','resources':cap['preconditions'],'reason':'沿用原能力宣告，實際操作另驗任務授權。'}] if cap['preconditions'] else []),
        'prompts':[],'state':{'schema_version':1,'migrations':[]},'documentation':'README.md','tests':'tests/cases.json'}
    cases=[]
    for sample in samples:
        calls=sample.get('platform_results',[])
        expected={'platform_request':{k:calls[0][k] for k in ('tool_id','arguments')}} if calls else {'result':sample['expected']}
        cases.append({'export':cap['id'],'input':sample['arguments'],'expected':expected})
    with tempfile.TemporaryDirectory(prefix='auren-tool-migration-') as temporary:
        directory=Path(temporary)/'source';directory.mkdir()
        files={'plugin.json':canonical(manifest),'legacy-capability.json':canonical(cap),'schemas/input.json':canonical(cap['signature']['input']),'schemas/output.json':canonical(output),
            'src/main.py':WORKER,'src/logic.py':code,'tests/cases.json':canonical(cases),'tests/platform-cases.json':canonical(samples),
            'README.md':'# '+cap['name']+'\n\n由已核准舊能力轉換；原品質、授權與平台轉接仍有效。舊原始碼保留供復原。\n'}
        for name,text in files.items():
            path=directory/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(text,encoding='utf-8')
        check(directory)
        archive=Path(temporary)/'converted.auren-plugin'
        with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as output:
            hashes={}
            for name in files:
                data=(directory/name).read_bytes();hashes[name]=sha256(data).hexdigest();output.writestr(name,data)
            output.writestr('integrity.json',canonical({'format_version':1,'id':identifier,'version':version,'files':hashes}))
        host.install(host.stage(archive)['upload_id'],True,False)
        host.control(identifier,'bind',True,contract)
        runtime.core.set_setting(key,{'hash':cap['hash'],'revision':previous.get('revision',0)+1,'package_id':identifier})
