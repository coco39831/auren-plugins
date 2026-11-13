from auren_infra.plugin_broker import invoke
class Provider:
    def __init__(self,c):self.context=c
    def invoke_package(self,*args):return invoke(self.context.require('auren.components/1').sandbox,*args)
def create(c,e):return Provider(c)
