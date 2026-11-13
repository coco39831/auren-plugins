"""官方高信任服務提供者；沿用原資料根與 Service 契約。"""
from auren_emotion.service import Service
def create(c,e):
    return Service(c.data_root,c.require('auren.kernel.vault/1'),c.require('auren.license/1').verify_capability,c.callback('audit'))
