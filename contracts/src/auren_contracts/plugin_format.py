"""統一插件格式 APS1，開發工具與正式宿主共用同一驗證器。"""
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import sys

BASE = Path(__file__).resolve().parent
KEYWORDS = {'$schema', '$id', '$defs', '$ref', 'title', 'description', 'type', 'const', 'enum',
            'properties', 'required', 'additionalProperties', 'items', 'minItems', 'maxItems',
            'uniqueItems', 'minLength', 'maxLength', 'minimum', 'maximum', 'pattern', 'oneOf'}


class Invalid(ValueError):
    """可以向開發者顯示的格式錯誤，不附帶任意插件的執行輸出。"""


def load(path):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise Invalid('JSON 包含重複欄位：' + key)
            result[key] = value
        return result
    try:
        if Path(path).stat().st_size > 1024 * 1024:
            raise Invalid('JSON 超過一 MiB：' + Path(path).name)
        return json.loads(Path(path).read_text(encoding='utf-8-sig'), object_pairs_hook=pairs,
                          parse_constant=lambda value: (_ for _ in ()).throw(Invalid('不允許非有限數值')))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise Invalid('無法讀取 JSON：' + Path(path).name) from exc


def schema_profile(schema, root=None, depth=0):
    """只接受明列的 APS1 Schema 子集，未知限制一律拒絕，避免默默略過。"""
    if not isinstance(schema, dict) or depth > 32 or set(schema) - KEYWORDS:
        raise Invalid('資料契約含未支援的 Schema 或深度過大')
    root = schema if root is None else root
    if '$ref' in schema:
        reference = schema['$ref']
        if not isinstance(reference, str) or not reference.startswith('#/$defs/') or reference[8:] not in root.get('$defs', {}):
            raise Invalid('Schema 只允許同檔案的 $defs 參照')
    for key in ('properties', '$defs'):
        if key in schema:
            if not isinstance(schema[key], dict): raise Invalid('Schema 屬性定義必須是物件')
            for child in schema[key].values(): schema_profile(child, root, depth + 1)
    if 'type' in schema and schema['type'] not in ('object','array','string','integer','number','boolean','null'):
        raise Invalid('Schema 型別不受支援')
    if 'required' in schema and (not isinstance(schema['required'], list) or any(not isinstance(x, str) for x in schema['required']) or len(set(schema['required'])) != len(schema['required'])):
        raise Invalid('Schema required 無效')
    if 'enum' in schema and (not isinstance(schema['enum'], list) or not schema['enum']): raise Invalid('Schema enum 無效')
    for key in ('minItems','maxItems','minLength','maxLength'):
        if key in schema and (type(schema[key]) is not int or schema[key] < 0): raise Invalid('Schema 長度限制無效')
    for key in ('minimum','maximum'):
        if key in schema and (type(schema[key]) not in (int,float) or not math.isfinite(schema[key])): raise Invalid('Schema 數值限制無效')
    if 'uniqueItems' in schema and type(schema['uniqueItems']) is not bool: raise Invalid('Schema uniqueItems 無效')
    if 'pattern' in schema:
        try: re.compile(schema['pattern'])
        except (re.error, TypeError) as exc: raise Invalid('Schema 正規表示式無效') from exc
    if 'items' in schema: schema_profile(schema['items'], root, depth + 1)
    if 'additionalProperties' in schema:
        child = schema['additionalProperties']
        if type(child) is not bool: schema_profile(child, root, depth + 1)
    if 'oneOf' in schema:
        if not isinstance(schema['oneOf'], list) or not schema['oneOf']: raise Invalid('Schema oneOf 無效')
        for child in schema['oneOf']: schema_profile(child, root, depth + 1)


def validate(value, schema, root=None, at='$', depth=0):
    root = schema if root is None else root
    if depth > 64: raise Invalid(at + '：資料或參照深度過大')
    if '$ref' in schema:
        validate(value, root['$defs'][schema['$ref'][8:]], root, at, depth + 1)
    if 'oneOf' in schema:
        matches = 0
        for child in schema['oneOf']:
            try: validate(value, child, root, at, depth + 1)
            except Invalid: continue
            matches += 1
        if matches != 1: raise Invalid(at + '：必須符合且只符合一種格式')
    kind = schema.get('type')
    kinds = {'object':isinstance(value,dict), 'array':isinstance(value,list), 'string':isinstance(value,str),
             'integer':type(value) is int, 'number':type(value) in (int,float), 'boolean':type(value) is bool, 'null':value is None}
    if kind and not kinds[kind]: raise Invalid(at + '：型別錯誤')
    encoded = lambda v: json.dumps(v, sort_keys=True, ensure_ascii=False, allow_nan=False)
    if 'const' in schema and encoded(value) != encoded(schema['const']): raise Invalid(at + '：固定值不符')
    if 'enum' in schema and encoded(value) not in [encoded(v) for v in schema['enum']]: raise Invalid(at + '：不在允許選項中')
    if isinstance(value, dict):
        if not set(schema.get('required', [])) <= set(value): raise Invalid(at + '：缺少必要欄位')
        for key, item in value.items():
            child = schema.get('properties', {}).get(key, schema.get('additionalProperties', True))
            if child is False: raise Invalid(at + '.' + key + '：未宣告欄位')
            if child is not True: validate(item, child, root, at + '.' + key, depth + 1)
    if isinstance(value, list):
        if len(value) < schema.get('minItems', 0) or len(value) > schema.get('maxItems', sys.maxsize): raise Invalid(at + '：清單長度錯誤')
        if schema.get('uniqueItems') and len({encoded(v) for v in value}) != len(value): raise Invalid(at + '：清單重複')
        for index, item in enumerate(value): validate(item, schema.get('items', {}), root, at + '/' + str(index), depth + 1)
    if isinstance(value, str):
        if len(value) < schema.get('minLength', 0) or len(value) > schema.get('maxLength', sys.maxsize): raise Invalid(at + '：字串長度錯誤')
        if 'pattern' in schema and not re.search(schema['pattern'], value): raise Invalid(at + '：字串格式錯誤')
    if type(value) in (int,float):
        if not math.isfinite(value) or value < schema.get('minimum', -math.inf) or value > schema.get('maximum', math.inf): raise Invalid(at + '：數值超出限制')


def file_in(root, name):
    if not isinstance(name, str) or not name or '\\' in name or ':' in name or '\x00' in name:
        raise Invalid('套件路徑無效')
    relative = PurePosixPath(name)
    if relative.is_absolute() or any(part in ('.', '..') for part in name.split('/')) or '' in name.split('/'):
        raise Invalid('套件路徑越界或不標準：' + name)
    reserved = {'CON','PRN','AUX','NUL',*(f'COM{i}' for i in range(1,10)),*(f'LPT{i}' for i in range(1,10))}
    if any(part.endswith((' ','.')) or part.split('.')[0].upper() in reserved or re.search(r'[<>"|?*\x00-\x1f]', part) for part in relative.parts):
        raise Invalid('套件路徑不相容 Windows：' + name)
    path = root.joinpath(*relative.parts)
    if not path.resolve().is_relative_to(root) or not path.is_file(): raise Invalid('找不到套件檔案：' + name)
    return path


def members(root, sealed=False):
    result = []
    names = set()
    for parent, dirs, files in os.walk(root, followlinks=False):
        for name in dirs + files:
            path = Path(parent) / name
            if path.is_symlink() or getattr(path.lstat(), 'st_file_attributes', 0) & 0x400:
                raise Invalid('套件不能含符號連結或重新解析點')
            relative = path.relative_to(root).as_posix()
            if name.lower() in ('.git','.venv','node_modules','__pycache__','.pytest_cache') or name.lower().startswith('.env'):
                raise Invalid('套件含開發環境或可能的秘密檔案：' + relative)
            if path.is_file():
                if relative == 'integrity.json' and sealed:
                    continue
                if path.suffix.lower() in ('.pem','.key','.pfx','.p12','.sqlite','.db','.pyc') or relative == 'integrity.json':
                    raise Invalid('套件含保留或敏感檔案：' + relative)
                file_in(root, relative)
                if relative.casefold() in names: raise Invalid('套件含大小寫衝突檔名')
                names.add(relative.casefold()); result.append((relative, path))
    return sorted(result)


def checked_schema(path):
    value = load(path); schema_profile(value); return value


def check_workflow(root, name, contracts):
    flow = load(file_in(root, name)); schema = checked_schema(BASE/'plugin_schemas/workflow-v1.schema.json'); validate(flow, schema)
    nodes = {node['id']: node for node in flow['nodes']}
    if len(nodes) != len(flow['nodes']): raise Invalid('流程節點識別碼重複')
    ancestors = {}
    def visit(identifier, stack):
        if identifier in stack: raise Invalid('流程依賴形成循環')
        if identifier not in nodes: raise Invalid('流程引用未知節點')
        if identifier in ancestors: return ancestors[identifier]
        node = nodes[identifier]
        if node['call'] not in contracts: raise Invalid('流程未宣告所用能力：' + node['call'])
        previous = set(node['needs'])
        for other in node['needs']: previous |= visit(other, stack | {identifier})
        ancestors[identifier] = previous
        return previous
    for identifier in nodes: visit(identifier, set())
    def check_binding(binding, allowed):
        if 'source' in binding and binding['source'] != 'input':
            source = binding['source'].split('.')[1]
            if source not in allowed: raise Invalid('流程資料引用沒有對應先行依賴')
    for node in nodes.values():
        for binding in node['arguments'].values(): check_binding(binding, ancestors[node['id']])
        if 'when' in node: check_binding(node['when']['value'], ancestors[node['id']])
    check_binding(flow['result'], set(nodes))
    return flow


def check(directory, sealed=False):
    root = Path(directory)
    if root.is_symlink(): raise Invalid('套件根目錄不能是符號連結')
    root = root.resolve()
    if not root.is_dir(): raise Invalid('套件目錄不存在')
    entries = members(root, sealed=sealed)
    manifest = load(file_in(root, 'plugin.json')); validate(manifest, checked_schema(BASE/'plugin_schemas/plugin-v1.schema.json'))
    exports = {row['id']: row for row in manifest['exports']}
    if len(exports) != len(manifest['exports']): raise Invalid('能力出口識別碼重複')
    if len({x['contract'] for x in manifest['exports']}) != len(exports): raise Invalid('同套件能力契約重複')
    if len({x['contract'] for x in manifest['requires']}) != len(manifest['requires']): raise Invalid('需求能力重複')
    if len({x['id'] for x in manifest['dependencies']}) != len(manifest['dependencies']) or any(x['id'] == manifest['id'] for x in manifest['dependencies']): raise Invalid('套件依賴重複或依賴自己')
    for name in (manifest['runtime']['entrypoint'], manifest['documentation'], manifest['tests']): file_in(root, name)
    for row in exports.values():
        for key in ('input_schema','output_schema'): checked_schema(file_in(root, row[key]))
    for key in ('config_schema',):
        if key in manifest: checked_schema(file_in(root, manifest[key]))
    if 'ui' in manifest: file_in(root, manifest['ui'])
    if 'setup' in manifest:
        contract=manifest['setup'].get('status_contract')
        if contract:
            service=next((row for row in exports.values() if row['contract']==contract),None)
            if not service or 'setup_status' not in service.get('methods',[]) or manifest['runtime'].get('transport')!='factory':raise Invalid('設定引導狀態需由本插件宣告的原生服務提供 setup_status')
        elif 'config_schema' not in manifest:raise Invalid('設定引導需提供設定契約或狀態服務')
    pages=manifest.get('pages',[])
    if len({page['id'] for page in pages})!=len(pages): raise Invalid('插件內容頁識別碼重複')
    for page in pages:
        if page['contract'] not in {row['contract'] for row in exports.values()}: raise Invalid('插件內容頁必須綁定自己宣告的能力')
        if page.get('renderer')=='builtin':
            if not manifest['id'].startswith('auren.official.') or page['id'] not in {'overview','chat','persona','memory','diary','user','todo','learning','knowledge','tools','tasks','emotion','roaming','sharing','speech','social'}: raise Invalid('此套件不能宣告內建內容頁')
        else:
            entry=page.get('ui') or manifest.get('ui')
            if not entry: raise Invalid('插件內容頁缺少圖形介面')
            html=file_in(root,entry)
            if html.suffix.lower() not in ('.html','.htm') or html.stat().st_size>1024*1024: raise Invalid('插件內容頁需為不超過一 MiB 的 HTML')
    for prompt in manifest['prompts']: file_in(root, prompt['path'])
    if len({x['from'] for x in manifest['state']['migrations']}) != len(manifest['state']['migrations']): raise Invalid('資料搬移起始版本重複')
    for migration in manifest['state']['migrations']:
        if migration['to'] != migration['from'] + 1 or migration['to'] > manifest['state']['schema_version']: raise Invalid('資料搬移版本必須逐級前進')
        file_in(root, migration['forward']); file_in(root, migration['rollback'])
    for name in manifest.get('workflows', []): check_workflow(root, name, {x['contract'] for x in manifest['requires'] + manifest['exports']})
    if manifest['runtime']['kind'] == 'declarative' and manifest['runtime']['entrypoint'] not in manifest.get('workflows', []): raise Invalid('宣告式入口必須列入 workflows')
    if manifest['runtime']['access'] == 'native' and not any(x['capability'] == 'host.native' for x in manifest['permissions']): raise Invalid('原生高權限插件必須宣告 host.native')
    if manifest['runtime'].get('transport') == 'factory' and (manifest['runtime']['access'] != 'native' or manifest['runtime']['kind'] != 'python'):
        raise Invalid('服務工廠只允許高信任原生 Python 插件')
    cases = load(file_in(root, manifest['tests']))
    if isinstance(cases,dict):
        # Service Protocol 沒有 JSON invoke；明列介面待整合驗收，不能偽造 {}→{} 案例。
        expected={identifier:row.get('methods',[]) for identifier,row in exports.items()}
        if manifest['runtime'].get('transport')!='factory' or set(cases)!={'format_version','kind','exports','integration_required'} or cases.get('format_version')!=1 or cases.get('kind')!='native-service' or cases.get('integration_required') is not True or cases.get('exports')!=expected or any(not methods for methods in expected.values()):
            raise Invalid('原生服務測試宣告必須逐項對應方法契約並標記需要整合驗收')
        return root, manifest, entries, []
    if not isinstance(cases, list) or not cases: raise Invalid('至少提供一個契約案例')
    for case in cases:
        if not isinstance(case, dict) or set(case) != {'export','input','expected'} or case.get('export') not in exports: raise Invalid('契約案例格式或能力出口無效')
        row = exports[case['export']]
        validate(case['input'], checked_schema(file_in(root, row['input_schema'])))
        validate(case['expected'], checked_schema(file_in(root, row['output_schema'])))
    return root, manifest, entries, cases

