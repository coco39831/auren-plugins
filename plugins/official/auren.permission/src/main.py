"""官方高信任服務提供者；沿用原資料根與 Service 契約。"""
from auren_permission.service import Service
def create(c,e):
    value=Service(c.data_root,c.require('auren.kernel.events/1'),c.require('auren.kernel.vault/1'),c.callback('cancel_task'),c.callback('audit'))
    value.attach_history(c.require('auren.memory/1'))
    return value
