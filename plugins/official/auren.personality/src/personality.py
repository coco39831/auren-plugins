"""統一人格入口；四種模式沿用既有塑形與成長機制，不改寫歷史資料。"""
from auren_contracts.models import Source,Denied,DomainError
from auren_contracts.features import PERSONALITY_QA


class Personality:
    def __init__(self,runtime):self.r=runtime

    def setup_status(self):return {'ready':self.state()['configured']}

    def state(self):
        identity=self.r.emotion.identity();modes=['void','seed','template']
        try:self.r.license.require_feature(PERSONALITY_QA);modes.append('portrait')
        except DomainError:pass
        return {'identity':{key:identity[key] for key in ('id','name','mode','created_at') if key in identity},
                'configured':identity.get('mode')!='unconfigured' and bool(self.r.emotion.get_setting('creation')),
                'available_modes':modes,'templates':[{key:row[key] for key in ('id','name','description')} for row in self.r.emotion.creation_templates()],
                'growth_enabled':self.r.emotion.get_setting('growth_enabled',False)}

    def initialize(self,value,actor):
        if actor.source!=Source.HUMAN or value.get('confirmed') is not True:raise Denied('請由使用者確認人格模式')
        with self.r.lock:
            if self.state()['configured']:raise Denied('初始人格已確定，不能重新選擇模式')
            identity=self.r.emotion.identity()
            if identity.get('mode')!='unconfigured':raise Denied('請先建立 Agent')
            if self.r.scheduler.running:raise Denied('請先停止執行中的任務，再設定人格模式')
            if value.get('mode')=='portrait':self.r.license.require_feature(PERSONALITY_QA)
            request={**value,'name':identity['name']}
            with self.r.personality_seed.claim(request,actor) as profile:
                self.r.emotion.create_identity(request,actor,profile)
            return self.state()

    def set_growth(self,enabled,actor):
        if not self.state()['configured']:raise Denied('請先在人格插件選擇模式')
        return self.r.emotion.set_growth(enabled,actor)
