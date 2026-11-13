"""官方高信任服務提供者；沿用原資料根與 Service 契約。"""
from auren_security.service import Service
from auren_license.service import unb64
def create(c,e):
    value=c.options; license=c.require('auren.license/1')
    return Service(c.data_root,vault=c.require('auren.kernel.vault/1'),verify_unlock=license.verify_unlock,upload=c.callback('upload_event') if value.get('event_url') else None,event_public_key=unb64(value['event_public_key']) if value.get('event_public_key') else None,verify_review=lambda token:license.verify_management(token,'security_review'))
