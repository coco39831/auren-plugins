"""官方高信任服務提供者；沿用原資料根與 Service 契約。"""
"""獨立的模型連線服務；全域預設與 Agent 覆寫使用不同設定命名空間。"""
from hashlib import sha256
import time
from auren_contracts.models import DomainError,Denied,canonical,uid
from auren_infra.model import ModelClient,normalize_config,is_external
from auren_infra.model_providers import discover_models

class Delegate:
    plugin_managed_lifecycle = False
    def __init__(self,context): object.__setattr__(self,'_kernel',context.require('auren.kernel.application/1'))
    def __getattr__(self,name): return self._kernel.get(name)
    def __setattr__(self,name,value): self._kernel.set(name,value)

class ModelSettings(Delegate):
    def setup_status(self):return {'ready':self.model_configured()}
    def global_settings(self):return DefaultModel(self._kernel._runtime)
    def model_options(self):
        from auren_contracts.configuration import CATALOG
        return {key:self.core.get_setting('engine.'+key,CATALOG['core'][key]['default']) for key in ('model_output_tokens','model_timeout')}
    def effective_model(self):
        mode=self.core.get_setting('model_mode','override' if self.core.get_setting('model') else 'inherit')
        if mode=='disabled': return None
        if mode=='inherit' and getattr(self,'default_model',None): return self.default_model.effective_model()
        return self.core.get_setting('model')
    def model_configured(self):
        mode=self.core.get_setting('model_mode','override' if self.core.get_setting('model') else 'inherit')
        if mode=='disabled': return False
        if mode=='inherit' and getattr(self,'default_model',None): return self.default_model.model_configured()
        return bool(self.core.get_setting('model'))

    def model(self,enforce_license=True):
        from .gateway import GatewayClient
        return GatewayClient(self._kernel,self._model_raw(enforce_license))

    def _model_raw(self,enforce_license=True):
        fallback=getattr(self,'default_model',None)
        mode=self.core.get_setting('model_mode','override' if self.core.get_setting('model') else 'inherit')
        if fallback and mode=='inherit':
            if enforce_license and is_external(fallback.core.get_setting('model')): self.license.require_feature('external_model')
            client=fallback.model(False)
            # 共用模型連線，不把另一個 Agent 的插件管線套進目前 Agent。
            client=getattr(client,'direct',client)
            client.options=self.model_options()
            client.diagnostics=self.diagnostics
            return client
        # 切換廠商時，位址、金鑰與同意必須取自同一份設定快照。
        with self.lock:
            config=self.core.get_setting('model')
            secret=self.core.get_setting('model_key')
            consent=self.core.get_setting('external_model_consent',False)
            if enforce_license and is_external(config): self.license.require_feature('external_model')
            if config and config.get('provider')=='llama_embedded': config=self.managed_llama.connect(config)
            return ModelClient(config,consent,self.vault.untext(secret) if secret else None,options=self.model_options(),diagnostics=self.diagnostics)

    def _model_connections(self):
        saved=self.core.get_setting('model_connections')
        if saved is not None: return saved
        previous=self.core.get_setting('model')
        return [{'id':'legacy','config':previous,'key':self.core.get_setting('model_key'),'external_consent':self.core.get_setting('external_model_consent',False),'discovery_consent':False,'models':[]}] if previous else []

    def model_connections(self):
        rows=self._model_connections()
        primary=self.core.get_setting('primary_model_id','legacy' if self.core.get_setting('model') else None)
        return {'connections':[{'id':row['id'],**row['config'],'external_consent':row['external_consent'],'discovery_consent':row.get('discovery_consent',False),'key_configured':bool(row['key']),'models':row.get('models',[]),'primary':row['id']==primary} for row in rows],'primary_model_id':primary}

    def _model_input(self,value,require_model=False):
        consent=value.get('external_consent') is True
        discovery=value.get('discovery_consent') is True
        config=normalize_config(value,consent or discovery,require_model=require_model)
        rows=self._model_connections(); previous=next((row for row in rows if row['id']==value.get('profile_id')),None)
        if value.get('profile_id') and not previous: raise Denied('模型連線不存在，請重新載入')
        same=previous and all(previous['config'].get(field)==config.get(field) for field in ('provider','vendor','base_url','executable','model_path'))
        key=value.get('key')
        if key is None or key=='': key=self.vault.untext(previous['key']) if same and previous['key'] else None
        if key is not None and (not isinstance(key,str) or len(key)>4096 or any(ord(c)<32 for c in key)): raise DomainError('API 金鑰格式無效')
        if config.get('vendor') not in (None,'custom') and not key: raise DomainError('請輸入選定廠商的 API 金鑰')
        return config,key,previous,consent,discovery

    def _model_signature(self,config,key):
        identity={name:value for name,value in config.items() if name!='model'}
        return sha256(canonical([identity,sha256((key or '').encode()).hexdigest()]).encode()).hexdigest()

    def discover_model_connection(self,value):
        if value.get('discovery_consent') is not True: raise Denied('請先同意以 API 金鑰向選定服務查詢模型清單')
        config,key,previous,consent,discovery=self._model_input(value)
        models=discover_models(config,key)
        identifier=uid()
        with self.lock:
            self.model_discoveries={name:row for name,row in self.model_discoveries.items() if row['expires']>time.monotonic()}
            if len(self.model_discoveries)>=20: self.model_discoveries.pop(next(iter(self.model_discoveries)))
            self.model_discoveries[identifier]={'signature':self._model_signature(config,key),'models':models,'expires':time.monotonic()+900}
        return {'discovery_id':identifier,'models':models,'message':'清單來自選定服務；讀取清單不會執行推理或傳送對話。'}

    def configure_model(self,value):
        primary=value.get('make_primary',True)
        if type(primary) is not bool: raise DomainError('主要模型選項必須為勾選狀態')
        with self.lock:
            config,key,previous,consent,discovery=self._model_input(value,require_model=primary)
            if primary and is_external(config) and not consent: raise Denied('設為主要模型前，請同意將任務內容傳送至選定服務')
            models=previous.get('models',[]) if previous and self._model_signature(previous['config'],self.vault.untext(previous['key']) if previous['key'] else None)==self._model_signature(config,key) else []
            receipt=self.model_discoveries.get(value.get('discovery_id'))
            if receipt:
                if receipt['expires']<=time.monotonic() or receipt['signature']!=self._model_signature(config,key): raise Denied('模型清單與目前連線不符或已過期，請重新讀取')
                models=receipt['models']
            if config.get('vendor'):
                if not models: raise Denied('請先讀取這組連線的模型清單')
                if config.get('model') and not any(row['id']==config['model'] and row['selectable'] for row in models): raise Denied('選擇的模型不在可用文字推理清單中')
            rows=self._model_connections(); identifier=previous['id'] if previous else uid()
            if not previous and len(rows)>=50: raise DomainError('最多保存 50 組模型連線')
            encrypted=self.vault.text(key) if key else None
            record={'id':identifier,'config':config,'key':encrypted,'external_consent':consent,'discovery_consent':discovery,'models':models}
            rows=[record if row['id']==identifier else row for row in rows] if previous else rows+[record]
            current=self.core.get_setting('primary_model_id','legacy' if self.core.get_setting('model') else None)
            with self.core.db.transaction():
                self.core.set_setting('model_connections',rows)
                if primary or current==identifier:
                    self.core.set_setting('model_mode','override')
                    self.managed_llama.close()
                    self.core.set_setting('primary_model_id',identifier if primary else None)
                    self.core.set_setting('model',config if primary else None)
                    self.core.set_setting('model_key',encrypted if primary else None)
                    self.core.set_setting('external_model_consent',consent if primary else False)
                elif self.core.get_setting('primary_model_id') is None and current:
                    self.core.set_setting('primary_model_id',current)
        self.audit.append('owner','model.configure',{'provider':config['provider'],'vendor':config.get('vendor'),'external_consent':consent,'primary':primary,'profile_id':identifier})
        return {'done':True,'profile_id':identifier,'primary':primary}

    def select_primary_model(self,identifier,model,consent):
        row=next((item for item in self._model_connections() if item['id']==identifier),None)
        if not row: raise Denied('模型連線不存在')
        return self.configure_model({**row['config'],'model':model,'profile_id':identifier,'make_primary':True,'external_consent':consent is True,'discovery_consent':row.get('discovery_consent',False)})

    def disable_model(self):
        with self.lock,self.core.db.transaction():
            self.managed_llama.close()
            self.core.set_setting('model_mode','disabled')
            self.core.set_setting('model',None); self.core.set_setting('model_key',None)
            self.core.set_setting('primary_model_id',None); self.core.set_setting('model_connections',[])
            self.core.set_setting('external_model_consent',False)
            self.model_discoveries.clear()
        self.audit.append('owner','model.disable',{})


class NamespacedSettings:
    def __init__(self,service,prefix): self.service=service; self.prefix=prefix; self.db=service.db
    def get_setting(self,name,default=None): return self.service.get_setting(self.prefix+name,default)
    def set_setting(self,name,value): return self.service.set_setting(self.prefix+name,value)

class ModelAudit:
    def __init__(self,audit): self.audit=audit
    def append(self,actor,action,payload): return self.audit.append(actor,'system.'+action,payload)

class DefaultModel(ModelSettings):
    # 全域連線設定是獨立命名空間，不把自己的狀態寫回 Agent 組合根。
    def __setattr__(self,name,value):object.__setattr__(self,name,value)
    def __getattr__(self,name):
        if name=='default_model':return None
        return getattr(self.host,name)
    def __init__(self,host):
        from auren_infra.model import ManagedLlama
        self.host=host;self.agent_id='system'
        self.core=NamespacedSettings(host.core,'system.model.')
        self.vault=host.vault; self.audit=ModelAudit(host.audit); self.license=host.license
        self.lock=host.lock; self.managed_llama=ManagedLlama(); self.model_discoveries={}
        self.diagnostics=host.diagnostics

    def model(self,enforce_license=True):
        from .gateway import GatewayClient
        return GatewayClient(self,self._model_raw(enforce_license))


def create(context,export_id):
    return ModelSettings(context)
