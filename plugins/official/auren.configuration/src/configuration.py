"""框架參數僅由成長機制寫入；使用者只操作成長開關與模型選項。"""
from auren_contracts.configuration import CATALOG
from auren_contracts.models import Source, Denied, DomainError

MODEL_FIELDS = frozenset(('model_output_tokens', 'model_timeout'))

class AgentConfiguration:
    def __init__(self, runtime):
        self.runtime = runtime
        self._growth_permit = object()

    def service(self, module):
        if module not in CATALOG: raise DomainError('未知設定系統')
        return getattr(self.runtime, module)

    def delegated(self, module):
        return self.runtime.emotion.get_setting('growth_enabled', True)

    def delegations(self):
        return {module: self.delegated(module) for module in CATALOG}

    def panel(self):
        return {'growth_enabled': self.delegated('emotion'), 'message': '變化交給經歷，成長由你決定是否繼續。'}

    def delegate(self, *args, **kwargs):
        raise Denied('框架參數由成長機制統一管理，請使用成長模式開關')

    def configure(self, module, values, revision, actor=None, confirmed=False, *, permit=None):
        if permit is not self._growth_permit: raise Denied('框架參數只接受成長機制調整')
        if not self.runtime.emotion.get_setting('growth_enabled', True): raise Denied('成長模式已關閉，框架參數保持不變')
        service = self.service(module)
        before = service.engine_settings()
        result = service.configure_engine(values, revision)
        self.runtime.audit.append('growth', 'engine.configuration', {'module': module, 'before': {row['key']: row['value'] for row in before['fields'] if row['key'] in values}, 'after': values, 'revision': result['revision']})
        return result

    def model_panel(self):
        return {'options': {key: self.runtime.core.parameter(key) for key in MODEL_FIELDS}}

    def configure_model(self, values, actor):
        if actor.source != Source.HUMAN or not isinstance(values, dict) or not values or set(values) - MODEL_FIELDS: raise Denied('這裡只接受模型輸出長度與連線逾時調整')
        for key, value in values.items():
            if type(value) is not int or value < 1 or value > (1000000 if key == 'model_output_tokens' else 3600): raise DomainError('請填入有效的模型選項')
        self.runtime.core.configure_engine(values, self.runtime.core.get_setting('engine.revision', 0))
        self.runtime.audit.append(actor.subject, 'model.options', {'keys': sorted(values)})
        return self.model_panel()
