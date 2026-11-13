"""日記內容入口；透過記憶契約保留原儲存與壓縮，不跨讀資料表。"""
from auren_contracts.models import Status,Denied,DomainError


class Diary:
    def __init__(self,context):
        self.context=context
        self.memory=context.require('auren.memory/1')
        self.license=context.require('auren.license/1')

    def health(self):return self.memory.health()

    def status(self):
        return Status('diary',self.health(),1,'日記保存在原本的記憶庫',{'entries':self.memory.page('diary',0,1)['total']})

    def close(self):pass

    @staticmethod
    def visible(record):
        return {key:record[key] for key in ('id','layer','text','created_at','archived','metadata') if key in record}

    def invoke(self,value,context):
        self.license.require_feature('memory')
        operation=value['operation'];offset=value.get('offset',0);limit=value.get('limit',50)
        if operation=='list':
            page=self.memory.page('diary',offset,limit)
            return {'records':[self.visible(row) for row in page['entries']],'total':page['total'],'offset':offset,'limit':limit}
        if operation in ('search','export'):
            # 原匯出服務先固定記錄識別碼；日記入口只回傳 diary 層，沒有默默截斷搜尋。
            records=[];query=value.get('query','').casefold()
            for record in self.memory.export_records():
                self.context.checkpoint()
                if record['layer']=='diary' and (operation=='export' or query in record['text'].casefold()):records.append(record)
            if operation=='export':return {'records':records,'total':len(records)}
            records.sort(key=lambda row:(row['created_at'],row['id']),reverse=True)
            return {'records':[self.visible(row) for row in records[offset:offset+limit]],'total':len(records),'offset':offset,'limit':limit}
        if operation in ('append','correct'):
            text=value.get('text','')
            if not text.strip():raise DomainError('日記內容不能為空白')
        if operation=='append':
            identifier=self.memory.append(value['text'],'diary','event',{'period':'task'})
        else:
            identifier=value.get('id','');record=self.memory.read(identifier)
            if record['layer']!='diary':raise Denied('日記插件只能處理日記內容')
            if operation=='correct':identifier=self.memory.correct(identifier,value['text'])
            elif operation=='archive':self.memory.archive(identifier)
            elif operation=='restore':self.memory.restore_hot(identifier)
            else:raise DomainError('不支援的日記操作')
        self.context.callback('audit')(context.get('subject','user'),'diary.'+operation,{'id':identifier})
        return {'done':True,'id':identifier}


def create(context,export_id):return Diary(context)
