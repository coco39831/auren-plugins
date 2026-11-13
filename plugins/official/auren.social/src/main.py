"""共用一個本機社交資料庫，每個 Agent 仍各自安裝、驗證身分與選用模型。"""
from .social import Social
from .hub import Hub


def create(context,export_id):
    directory=context.require('auren.kernel.agent-directory/1')
    with directory.host.lock:
        hub=getattr(directory,'_social_plugin_hub',None)
        if hub is None or hub.closed:
            hub=Hub(directory);directory._social_plugin_hub=hub
        return Social(context,hub)
