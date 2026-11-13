"""官方原生 Service Protocol 入口。"""
from .plugins import Plugins
def create(c,e): return Plugins(c.require('auren.kernel.application/1'))
