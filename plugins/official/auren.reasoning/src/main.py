from copy import deepcopy
class Provider:
    def invoke(self,value,context):
        # 預設保留既有任務 Skill 已組裝的思考上下文，不額外注入人格或新目標。
        return deepcopy(value)
def create(c,e):return Provider()
