from .sharing import Sharing

def create(c,e):
    return Sharing(c.require('auren.kernel.agent-directory/1'))
