"""官方高信任服務提供者；沿用原資料根與 Service 契約。"""
from auren_infra.sandbox import Service
def create(c,e): return Service(c.data_root)
