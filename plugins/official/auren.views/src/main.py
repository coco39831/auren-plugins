"""官方原生 Service Protocol 入口。"""
from .presentation import UserViews
def create(c,e): return UserViews(c.require('auren.kernel.application/1'))
