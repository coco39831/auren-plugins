"""官方原生 Service Protocol 入口。"""
from .roaming import Roaming
def create(c,e): return Roaming(c.require('auren.kernel.application/1'))
