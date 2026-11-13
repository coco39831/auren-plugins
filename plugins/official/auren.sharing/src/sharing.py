"""指定 Agent 配對與資料類別的雙向共享；所有讀寫皆經來源 Service。"""
import json
from auren_contracts.models import Actor,Source,Denied,DomainError,Change,RetrievalBudget,canonical

CATEGORIES={'diary':'日記庫','log':'操作日誌','knowledge':'知識庫','tools':'工具庫','emotion':'情感狀態','user':'我記得的事','persona':'人格','todo':'待辦庫','rules':'規則庫','curiosity':'好奇心','learning':'學習紀錄','memory':'原始記憶'}
LAYERS={'diary':'diary','log':'log','user':'user','todo':'todo','memory':'raw'}

class Sharing:
    def __init__(self,directory): self.directory=directory; self.host=directory.host
    def links(self):
        value=self.host.core.get_setting('sharing_links')
        return json.loads(self.host.vault.untext(value)) if value else []
    def save(self,links): self.host.core.set_setting('sharing_links',self.host.vault.text(canonical(links)))
    def status(self,identifier):
        result={}
        for link in self.links():
            if identifier in link['agents']:
                peer=next(a for a in link['agents'] if a!=identifier)
                result.setdefault(peer,[]).append(link['category'])
        return {'categories':CATEGORIES,'outgoing':result,'incoming':result,'bidirectional':True}
    def configure(self,identifier,peer,category,enabled,actor):
        if actor.source!=Source.HUMAN or type(enabled) is not bool: raise Denied('共享設定只接受人工明確選擇')
        if not isinstance(peer,str) or not isinstance(category,str) or identifier==peer or category not in CATEGORIES: raise DomainError('共享對象或類別無效')
        self.directory.resolve(identifier); self.directory.resolve(peer)
        pair=sorted([identifier,peer])
        with self.directory.lock:
            links=[link for link in self.links() if not(link['agents']==pair and link['category']==category)]
            if enabled: links.append({'agents':pair,'category':category})
            self.save(links)
            for item in pair: self.directory.resolve(item).audit.append(actor.subject,'sharing.changed',{'agents':pair,'category':category,'enabled':enabled})
        return self.status(identifier)
    def source(self,viewer,owner,category):
        if not isinstance(owner,str) or not isinstance(category,str) or category not in CATEGORIES or viewer==owner: raise Denied('共享資料範圍無效')
        if category not in self.status(viewer)['incoming'].get(owner,[]): raise Denied('沒有這一類資料的共享權限，或權限已撤銷')
        self.directory.resolve(viewer)
        return self.directory.resolve(owner)
    def read(self,viewer,owner,category):
        source=self.source(viewer,owner,category)
        if category in LAYERS: data={'records':source.memory.list_records(layer=LAYERS[category],limit=200)}
        elif category in ('knowledge','tools'):
            graph=source.knowledge.overview(); data={'records':graph['knowledge' if category=='knowledge' else 'tools']}
        elif category=='emotion': data={'records':[source.emotion.state()]}
        elif category=='persona': data={'records':[source.emotion.profiles()['profiles'][0]]}
        elif category=='rules': data={'records':source.permission.rules()}
        elif category=='curiosity': data={'records':source.learning.interests()}
        else: data={'records':source.learning.list_candidates()}
        return {**data,'owner':owner,'owner_name':source.emotion.identity()['name'],'category':category}
    def write(self,viewer,owner,category,value,actor):
        if actor.source!=Source.HUMAN or value.get('confirmed') is not True: raise Denied('請先確認修改共享來源資料')
        source=self.source(viewer,owner,category); operation=value.get('operation','update'); identifier=value.get('id')
        with self.directory.lock,source.lock:
            # 每次寫入前重新檢查連線，撤銷後不再取得來源執行權。
            self.source(viewer,owner,category)
            if category in LAYERS:
                if identifier and source.memory.read(identifier)['layer']!=LAYERS[category]: raise Denied('資料不屬於已共享類別')
                text=value.get('text')
                if not isinstance(text,str) or not text.strip() or len(text)>32000: raise DomainError('請輸入有效內容')
                result={'id':source.memory.correct(identifier,text) if identifier else source.memory.append(text,LAYERS[category],metadata={'shared_from':viewer})}
            elif category in ('knowledge','tools'):
                data=value.get('data',{})
                if not isinstance(data,dict): raise DomainError('共享資料格式無效')
                if category=='knowledge':
                    if set(data)-{'id','content_raw','content_summary','name','status','type','language','context_tags'}:
                        raise Denied('共享可更正知識內容，不能人工改寫內部數值')
                    previous=next((row for row in source.knowledge.overview()['knowledge'] if row['id']==identifier),{})
                    result={'id':source.knowledge.add_knowledge({**previous,**data})}
                else:
                    source.gate.require_agent_execution(data.get('code',''))
                    result={'id':source.knowledge.add_tool(data,value.get('knowledge_ids',[]))}
            elif category=='rules':
                if operation=='confirm':
                    receipt=source.core.get_setting('shared_rule.'+str(identifier))
                    if not receipt or receipt!=viewer: raise Denied('規則確認不對應這次共享操作')
                    result=source.permission.confirm(identifier,actor,True)
                    source.core.set_setting('shared_rule.'+identifier,None)
                else:
                    result=source.permission.propose_rule(value.get('text',''),'global',{},actor,identifier)
                    source.core.set_setting('shared_rule.'+result['pending_id'],viewer)
            elif category=='curiosity': result=source.learning.notice(value.get('text',''),.5,0,.5)
            elif category=='learning':
                if not identifier: raise DomainError('請選擇學習紀錄')
                source.learning.feedback(identifier,value.get('positive') is True); result={'done':True}
            elif category=='emotion': result=source.emotion.on_event('user.praise' if value.get('positive') is True else 'user.criticism','shared:'+viewer,.3)
            else: raise Denied('人格建立後不能人工改寫；共享只供理解對方，人格演化仍由來源 Agent 的成長機制管理')
            for child in (source,self.directory.resolve(viewer)):
                child.audit.append(actor.subject,'sharing.updated',{'owner':owner,'viewer':viewer,'category':category,'operation':operation,'record_id':identifier})
            return result
    def context(self,viewer,query):
        """每次檢索即時檢查共享，不建立隱藏的永久副本或遞迴共享。"""
        result=[]
        for owner,categories in self.status(viewer)['incoming'].items():
            try: source=self.directory.resolve(owner)
            except DomainError: continue
            for category in categories:
                if category in LAYERS:
                    records=[row for row in source.memory.search(query,20) if row['layer']==LAYERS[category]][:4]
                elif category=='knowledge': records=list(source.knowledge.retrieve(query,RetrievalBudget(tokens=2500),False).knowledge)
                elif category=='tools': records=[{'id':'shared:'+owner+':'+row['id'],'name':row['name'],'capabilities':row['capabilities']} for row in source.knowledge.overview()['tools'] if row['status']=='active'][:10]
                else: records=self.read(viewer,owner,category)['records'][:5]
                if records: result.append({'source_agent':owner,'category':category,'records':records})
        return result
