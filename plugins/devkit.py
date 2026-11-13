"""統一插件開發工具；檢查及打包不執行插件，試跑須明確允許原生程序。"""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile

BASE = Path(__file__).resolve().parent
# 原始碼使用者不需先安裝 wheel；正式產品由同一 contracts 套件提供。
try:
    from auren_contracts.plugin_format import (Invalid, load, schema_profile, validate, file_in, members, checked_schema, check_workflow, check)
except ModuleNotFoundError as exc:
    if exc.name not in ('auren_contracts', 'auren_contracts.plugin_format'):
        raise
    sys.path.insert(0, str(BASE.parent/'contracts'/'src'))
    from auren_contracts.plugin_format import (Invalid, load, schema_profile, validate, file_in, members, checked_schema, check_workflow, check)


def create(directory, identifier, access='brokered'):
    if not re.fullmatch(r'[a-z][a-z0-9-]*(\.[a-z][a-z0-9-]*)+', identifier): raise Invalid('識別碼需使用小寫反向網域格式')
    if access not in ('brokered','native'):raise Invalid('執行權限格式無效')
    target = Path(directory)
    if target.exists(): raise Invalid('目標已存在，不覆寫既有插件')
    shutil.copytree(BASE/'templates/python', target)
    manifest = load(target/'plugin.json'); manifest['id'] = identifier
    manifest['runtime']['access']=access
    if access=='native':manifest['permissions'].append({'capability':'host.native','resources':['local-computer'],'reason':'由使用者明確同意在本機直接執行此插件，不宣稱具有隔離。'})
    (target/'plugin.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    check(target)


def pack(directory, output):
    root, manifest, entries, _ = check(directory)
    target = Path(output).resolve()
    if target.is_relative_to(root) or target.exists(): raise Invalid('輸出需在套件外，且不能覆寫現有檔案')
    if target.suffix != '.auren-plugin': raise Invalid('副檔名必須是 .auren-plugin')
    target.parent.mkdir(parents=True, exist_ok=True)
    hashes = {}
    # 使用臨時快照避免雜湊與封裝之間來源變更，只有全部成功才公布成品。
    with tempfile.TemporaryDirectory(prefix='auren-plugin-pack-') as temporary:
        stage = Path(temporary)
        for name, path in entries:
            destination = stage/name; destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, destination)
            with destination.open('rb') as stream: hashes[name] = hashlib.file_digest(stream, 'sha256').hexdigest()
        check(stage)
        integrity = {'format_version':1, 'id':manifest['id'], 'version':manifest['version'], 'files':hashes}
        (stage/'integrity.json').write_text(json.dumps(integrity, ensure_ascii=False, sort_keys=True)+'\n', encoding='utf-8')
        artifact = stage/'package.zip'
        with zipfile.ZipFile(artifact, 'w', zipfile.ZIP_DEFLATED) as archive:
            for name in sorted([*hashes, 'integrity.json']): archive.write(stage/name, name)
        with artifact.open('rb') as source, target.open('xb') as destination: shutil.copyfileobj(source, destination)
    return target


def probe(directory, allow_native=False):
    if not allow_native: raise Invalid('試跑會執行本機程式；僅對可信插件加上 --allow-native')
    root, manifest, _, cases = check(directory)
    if manifest['runtime'].get('transport')=='factory': raise Invalid('原生服務工廠需以宿主服務契約測試驗證，不能當成 stdio 試跑')
    if manifest['runtime']['kind'] != 'python': raise Invalid('此開發試跑工具目前只執行 Python 入口，其他格式僅靜態檢查')
    requests = [{'jsonrpc':'2.0','id':0,'method':'plugin.initialize','params':{'protocol_version':1}}]
    context = {'agent_id':'development', 'task_id':'development', 'run_id':'development', 'config':{}}
    requests += [{'jsonrpc':'2.0','id':i+1,'method':'plugin.invoke','params':{'export':case['export'],'input':case['input'],'context':context}} for i, case in enumerate(cases)]
    requests.append({'jsonrpc':'2.0','id':len(cases)+1,'method':'plugin.shutdown','params':{}})
    payload = ''.join(json.dumps(x, ensure_ascii=False)+'\n' for x in requests).encode('utf-8')
    # 此模式不是隔離器；不向插件傳遞目前程序中的 API 金鑰等環境變數。
    env = {key:value for key,value in os.environ.items() if key.upper() in ('SYSTEMROOT','WINDIR','TEMP','TMP','PATH','LANG','LC_ALL')}
    try:
        with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as error:
            reply = subprocess.run([sys.executable, '-I', '-B', str(file_in(root, manifest['runtime']['entrypoint']))],
                cwd=root, input=payload, stdout=output, stderr=error, timeout=30, env=env,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            if reply.returncode: raise Invalid('插件試跑程序失敗；未將任意輸出寫入開發工具紀錄')
            if output.tell() > 1024*1024: raise Invalid('試跑輸出超過一 MiB')
            output.seek(0)
            responses = [json.loads(line) for line in output.read().decode('utf-8').splitlines()]
    except (subprocess.TimeoutExpired, UnicodeError, json.JSONDecodeError) as exc: raise Invalid('插件逾時或回傳協定無效') from exc
    if len(responses) != len(requests): raise Invalid('插件回傳數量與請求不符')
    for request, response in zip(requests, responses):
        if not isinstance(response, dict) or response.get('jsonrpc') != '2.0' or type(response.get('id')) is not int or response['id'] != request['id'] or 'error' in response or 'result' not in response: raise Invalid('插件回傳識別碼或結果無效')
    if responses[0]['result'] != {'protocol_version':1} or responses[-1]['result'] != {'status':'stopped'}: raise Invalid('插件生命週期回應無效')
    exports = {row['id']:row for row in manifest['exports']}
    for case, response in zip(cases, responses[1:-1]):
        validate(response['result'], checked_schema(file_in(root, exports[case['export']]['output_schema'])))
        if response['result'] != case['expected']: raise Invalid('插件實際結果與契約案例不符')
    return len(cases)


def main(argv=None):
    parser = argparse.ArgumentParser(description='Auren 統一插件開發工具，無需額外套件')
    sub = parser.add_subparsers(dest='command', required=True)
    new = sub.add_parser('new'); new.add_argument('directory'); new.add_argument('--id', required=True);new.add_argument('--access',choices=('brokered','native'),default='brokered')
    verify = sub.add_parser('validate'); verify.add_argument('directory')
    package = sub.add_parser('pack'); package.add_argument('directory'); package.add_argument('--output', required=True)
    trial = sub.add_parser('probe'); trial.add_argument('directory'); trial.add_argument('--allow-native', action='store_true')
    args = parser.parse_args(argv)
    try:
        if args.command == 'new': create(args.directory, args.id,args.access); result = {'created':args.directory}
        elif args.command == 'validate': result = {'validated':check(args.directory)[1]['id'], 'executed':False}
        elif args.command == 'pack': result = {'package':str(pack(args.directory, args.output))}
        else: result = {'cases_passed':probe(args.directory, args.allow_native), 'isolated':False}
        print(json.dumps(result, ensure_ascii=False)); return 0
    except (Invalid, OSError) as exc:
        print(json.dumps({'error':str(exc)}, ensure_ascii=False), file=sys.stderr); return 1


if __name__ == '__main__':
    raise SystemExit(main())
