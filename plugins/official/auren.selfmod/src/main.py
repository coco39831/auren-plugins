"""官方高信任服務提供者；沿用原資料根與 Service 契約。"""
from auren_selfmod.service import Service
def create(c,e):
    return Service(c.data_root,c.require('auren.kernel.gate/1'),c.require('auren.permission/1'),c.require('auren.kernel.versions/1'),vault=c.require('auren.kernel.vault/1'),require_feature=c.require('auren.license/1').require_feature)
