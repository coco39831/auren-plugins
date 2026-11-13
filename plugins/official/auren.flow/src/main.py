"""預設流程透過原生延續執行；替換此能力即可替換該段任務策略。"""
class Provider:
    def __init__(self,context):self.context=context
    def invoke(self,value,context):return self.context.callback('flow.original')(value)
def create(context,export_id):return Provider(context)
