"""官方原生 Service Protocol 入口。"""
from .task_context import TaskContext
def create(c,e): return TaskContext(c.require('auren.kernel.application/1'),c.require('auren.kernel.context-quota/1'))
