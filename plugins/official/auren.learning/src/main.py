"""官方高信任服務提供者；沿用原資料根與 Service 契約。"""
from auren_learning.service import Service
from auren_infra.network import PublicFetcher
def create(c,e):
    return Service(c.data_root,c.require('auren.kernel.events/1'),c.require('auren.kernel.vault/1'),PublicFetcher(c.require('auren.permission/1').authorize),c.callback('validate_claim'))
