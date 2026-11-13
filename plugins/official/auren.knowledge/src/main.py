"""官方高信任服務提供者；沿用原資料根與 Service 契約。"""
from pathlib import Path
from auren_knowledge.service import Service
from auren_infra.embeddings import LocalE5
def create(c,e):
    value=c.options.get('embedding',{}); path=value.get('path')
    if path and not Path(path).is_absolute():path=Path(c.options['_resource_root'])/path
    return Service(c.data_root,embed=LocalE5(path,value.get('enabled',True)),admit_rule=c.callback('admit_rule'),audit=c.callback('audit'),protect=c.callback('persona_dependency'))
