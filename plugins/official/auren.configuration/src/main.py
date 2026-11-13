"""官方原生 Service Protocol 入口。"""
from .configuration import AgentConfiguration
def create(c,e): return AgentConfiguration(c.require('auren.kernel.application/1'))
