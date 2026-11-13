from copy import deepcopy
class Provider:
    def invoke(self,value,context):return deepcopy(value)
def create(c,e):return Provider()
