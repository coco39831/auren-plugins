"""Auren 插件 RPC v1 範本；標準輸出只傳送協定訊息。"""
import json
import sys


def invoke(export, arguments, context):
    if export != 'echo':
        raise ValueError('未知能力出口')
    if not isinstance(arguments, dict) or set(arguments) != {'text'} or not isinstance(arguments['text'], str) or len(arguments['text']) > 4096:
        raise ValueError('text 必須是長度不超過 4096 的字串')
    return {'text': arguments['text']}


def main():
    initialized = False
    while True:
        line = sys.stdin.buffer.readline(1024 * 1024 + 1)
        if not line:
            return
        if len(line) > 1024 * 1024:
            return 1
        identifier = None
        request = None
        error_code = -32700
        try:
            request = json.loads(line)
            error_code = -32600
            if not isinstance(request, dict) or request.get('jsonrpc') != '2.0' or type(request.get('id')) not in (str, int):
                raise ValueError('需要具有識別碼的 JSON-RPC 2.0 請求')
            identifier = request['id']
            method = request.get('method')
            error_code = -32601
            if method not in ('plugin.initialize','plugin.health','plugin.shutdown','plugin.invoke'):
                raise ValueError('不支援的方法')
            error_code = -32602
            if method == 'plugin.initialize':
                params = request.get('params')
                if initialized or not isinstance(params, dict) or set(params) != {'protocol_version'} or type(params['protocol_version']) is not int or params['protocol_version'] != 1:
                    raise ValueError('協定版本或生命週期順序無效')
                initialized = True
                result = {'protocol_version': 1}
            elif not initialized:
                raise ValueError('尚未完成初始化')
            elif method == 'plugin.health':
                result = {'status': 'ready'}
            elif method == 'plugin.shutdown':
                result = {'status': 'stopped'}
            elif method == 'plugin.invoke':
                params = request.get('params', {})
                result = invoke(params['export'], params['input'], params['context'])
            response = {'jsonrpc': '2.0', 'id': identifier, 'result': result}
        except (ValueError, KeyError, TypeError):
            response = {'jsonrpc': '2.0', 'id': identifier, 'error': {'code': error_code, 'message': '請求格式、方法或能力輸入無效'}}
        except Exception:
            response = {'jsonrpc': '2.0', 'id': identifier, 'error': {'code': -32603, 'message': '插件執行失敗'}}
        sys.stdout.buffer.write((json.dumps(response, ensure_ascii=False, allow_nan=False) + '\n').encode('utf-8'))
        sys.stdout.buffer.flush()
        if isinstance(request, dict) and request.get('method') == 'plugin.shutdown' and 'result' in response:
            return


if __name__ == '__main__':
    raise SystemExit(main())
