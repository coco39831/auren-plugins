"""官方高信任服務提供者；沿用原資料根與 Service 契約。"""
from auren_memory.service import Service
def create(c,e):
    return Service(c.data_root,c.require('auren.kernel.events/1'),c.require('auren.kernel.vault/1'))
