from .task_workflow import TaskWorkflow
from .flow_replay import FlowReplay
from .flow_validation import FlowValidation
def create(c,e):
    root=c.require('auren.kernel.application/1')
    if e=='replay':return FlowReplay(root)
    if e=='validation':return FlowValidation(root)
    return TaskWorkflow(root)
