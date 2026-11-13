"""短音訊轉寫；連線設定與金鑰只保存在此 Agent 的加密資料檔。"""
import base64
import binascii
import ipaddress
import json
from pathlib import Path
import re
import threading
from urllib.parse import urlsplit
import httpx
from auren_contracts.models import Denied, DomainError, NotReady, canonical

MAX_AUDIO=700*1024
EXTENSIONS={'wav','mp3','mp4','m4a','mpeg','mpga','ogg','flac','webm'}


class Speech:
    def __init__(self,context):
        self.context=context;self.vault=context.require('auren.kernel.vault/1')
        self.path=Path(context.data_root)/'plugin-data'/'auren.official.speech'/'connection.enc'
        self.lock=threading.RLock()
    def close(self):pass
    def saved(self):
        with self.lock:
            return json.loads(self.vault.untext(self.path.read_text(encoding='utf-8'))) if self.path.exists() else None
    def setup_status(self):return {'ready':bool(self.saved())}
    def status(self):
        value=self.saved()
        return {'ready':bool(value),'base_url':value['base_url'] if value else '',
                'model':value['model'] if value else '', 'language':value.get('language','') if value else '',
                'key_configured':bool(value and value.get('key')),'external':bool(value and value['external']),
                'max_audio_bytes':MAX_AUDIO}
    @staticmethod
    def endpoint(value):
        if not isinstance(value,str) or len(value)>2048 or any(ord(c)<33 for c in value):raise DomainError('語音服務位址格式無效')
        try:
            url=urlsplit(value.rstrip('/'));port=url.port
            if url.scheme not in ('http','https') or not url.hostname or url.username or url.password or url.query or url.fragment:raise ValueError
            loopback=url.hostname=='localhost'
            try:loopback=ipaddress.ip_address(url.hostname).is_loopback
            except ValueError:pass
            if url.scheme!='https' and not loopback:raise ValueError
        except ValueError:raise DomainError('請使用 HTTPS 語音服務，或本機 localhost／回環位址') from None
        return value.rstrip('/'),not loopback
    def configure(self,value,context):
        if context.get('source')!='human' or value.get('confirmed') is not True:raise Denied('語音連線需由使用者確認')
        base,external=self.endpoint(value.get('base_url'))
        if external and value.get('external_consent') is not True:raise Denied('請明確同意將選擇的音訊傳送到語音服務')
        model=value.get('model');language=value.get('language','')
        if not isinstance(model,str) or not model.strip() or len(model)>200 or any(ord(c)<32 for c in model):raise DomainError('請填寫語音模型名稱')
        if not isinstance(language,str) or language and not re.fullmatch('[a-z]{2,3}',language):raise DomainError('語音語言請留白，或使用 zh、en、ja、ko 等語言代碼')
        with self.lock:
            old=self.saved();key=value.get('key')
            if key is None:key=old.get('key') if old and old['base_url']==base else None
            if key is not None and (not isinstance(key,str) or len(key)>4096 or any(ord(c)<32 for c in key)):raise DomainError('API 金鑰格式無效')
            setting={'base_url':base,'model':model.strip(),'language':language,'key':key,'external':external}
            self.path.parent.mkdir(parents=True,exist_ok=True)
            temporary=self.path.with_suffix('.pending')
            temporary.write_text(self.vault.text(canonical(setting)),encoding='utf-8');temporary.replace(self.path)
        self.context.callback('audit')(context.get('subject','user'),'speech.configured',{'external':external})
        return self.status()
    def transcribe(self,value,context):
        setting=self.saved()
        if not setting:raise NotReady('請先設定語音辨識連線，再選擇音訊或開始錄音')
        # 呼叫本身即為使用者送出此次音訊；背景 Agent 不得藉此錄音或外傳檔案。
        if context.get('source')!='human' or value.get('confirmed') is not True:raise Denied('請由使用者確認這次音訊轉寫')
        filename=value.get('filename','');encoded=value.get('audio','')
        if not isinstance(filename,str) or not re.fullmatch(r'[^\\/:\x00-\x1f]{1,100}\.(wav|mp3|mp4|m4a|mpeg|mpga|ogg|flac|webm)',filename,re.I):raise DomainError('請選擇支援的音訊檔案')
        if not isinstance(encoded,str) or len(encoded)>((MAX_AUDIO+2)//3)*4:raise DomainError('音訊超過 700 KiB，請分段轉寫')
        try:audio=base64.b64decode(encoded,validate=True)
        except (ValueError,binascii.Error):raise DomainError('音訊編碼無效') from None
        if not audio or len(audio)>MAX_AUDIO:raise DomainError('請選擇非空白、大小不超過 700 KiB 的音訊')
        self.context.checkpoint()
        headers={'Authorization':'Bearer '+setting['key']} if setting.get('key') else {}
        fields={'model':setting['model'],'response_format':'json'}
        if setting['language']:fields['language']=setting['language']
        try:
            # 不跟隨重新導向，不把系統代理、Cookie 或登入資料交給外部服務。
            with httpx.Client(timeout=httpx.Timeout(120,connect=10),trust_env=False,follow_redirects=False) as client:
                with client.stream('POST',setting['base_url']+'/audio/transcriptions',headers=headers,data=fields,files={'file':(filename,audio,'application/octet-stream')}) as response:
                    if response.status_code in (401,403):raise NotReady('語音服務拒絕授權，請檢查 API 金鑰')
                    if response.status_code==429:raise NotReady('語音服務目前額度不足或請求過多')
                    if not 200<=response.status_code<300:raise NotReady('語音服務未完成轉寫，請確認位址、模型與支援格式')
                    blocks=[];size=0
                    for block in response.iter_bytes():
                        size+=len(block)
                        if size>1024*1024:raise NotReady('語音服務回應過大，請縮短音訊後重試')
                        blocks.append(block)
                    result=json.loads(b''.join(blocks))
            text=result.get('text') if isinstance(result,dict) else None
            if not isinstance(text,str):raise NotReady('語音服務回應缺少文字結果')
        except (httpx.HTTPError,ValueError):raise NotReady('語音服務連線或回應格式錯誤，請檢查後重試') from None
        self.context.checkpoint()
        self.context.callback('audit')(context.get('subject','user'),'speech.transcribed',{'audio_bytes':len(audio),'text_characters':len(text),'external':setting['external']})
        return {'text':text,'empty':not bool(text.strip())}
    def invoke(self,value,context):
        if value['operation']=='status':return self.status()
        if value['operation']=='configure':return self.configure(value,context)
        return self.transcribe(value,context)


def create(context,export_id):return Speech(context)
