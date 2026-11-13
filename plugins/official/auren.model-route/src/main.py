class Provider:
    def invoke(self,value,context):
        # 預設仍使用使用者指定的固定主要模型；多路由由替代插件決定。
        return {'connection_ref':'primary'}
def create(c,e):return Provider()
