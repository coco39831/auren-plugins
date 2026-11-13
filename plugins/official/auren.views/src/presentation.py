"""使用者呈現邊界：描述經歷與狀態，不輸出框架參數、實作來源或內部依據。"""
import json
from auren_contracts.models import DomainError

SYSTEM_TEXT = {
    'roaming': '推開一扇窗，去遠方拾起世界的新消息，再帶回值得一起細看的片段。',
    'emotion': '讓相處留下溫度，讓喜悅與低落都有來處，也有慢慢平復的時間。',
    'learning': '把偶然遇見的火花，慢慢磨成理解；把學會的事情，變成下一次伸手可及的本領。',
    'diary': '替平凡的日子留一盞燈，讓走過的故事不因時間而散落。',
    'tasks': '每一件答應的事，都有一段出發、停留與抵達的路。',
    'persona': '不是一張寫好的面具，而是記憶、感受與經歷交織成的樣子。',
}


def identity(value):
    return {key: value[key] for key in ('id', 'name', 'mode', 'created_at', 'archived', 'growth_enabled') if key in value}


def profiles(value):
    return {'active_id': value['active_id'], 'profiles': [identity(row) for row in value['profiles']]}


def task_summary(task):
    from auren_infra.installation_language import installed_language
    language=installed_language()
    value={key: task[key] for key in ('id', 'goal', 'subtasks', 'progress_summary', 'status', 'archived', 'created_at', 'updated_at') if key in task}
    # 只翻譯框架的固定狀態用語，保留目標、模型回覆與使用者內容。
    if value.get('progress_summary') in ('等待使用者授權','這件事還需要一些整理，已保留目前進度。'):
        value['progress_summary']=language.text(value['progress_summary'])
    state=task.get('workflow')
    if state:
        plan=state.get('plan')
        value['workflow']={'phase':state['phase'],'revision':state['revision'],'plan_revision':state['plan_revision'],
            'brief':task_brief(state.get('brief')),
            'require_user':state['require_user'],'questions':state['questions'],'checks':state['checks'],
            'step_checks':state.get('step_checks',{}),
            'knowledge_gap':state.get('knowledge_snapshot',{}).get('missing',False),
            'step_status':state['step_status'],'candidate':state.get('candidate'),
            'plan':{key:plan[key] for key in ('understanding','requirements','steps','criteria','assumptions')} if plan else None}
    if task['status']=='blocked':
        try:
            request=json.loads(task.get('context_snapshot',''))
            fields=('task_id','operation_id','action','resource')
            if isinstance(request,dict) and all(isinstance(request.get(key),str) for key in fields):
                value['permission_request']={key:request[key] for key in fields}
        except (ValueError,TypeError): pass
    return value


def task_brief(brief):
    if not brief: return None
    trigger=brief['trigger']
    return {**{key:brief[key] for key in ('title','goal','objectives','outline','open_questions','status','revision')},
        'context_information':brief.get('context_information',[]),
        'trigger':{key:trigger[key] for key in ('kind','summary','occurred_at','information')}}


def task_detail(value):
    from auren_infra.installation_language import installed_language
    language=installed_language()
    events = []
    private_actions = {'system_info', 'system_settings', 'component_info', 'component_settings', 'component_code', 'component_probe', 'component_merge'}
    for row in value['events']:
        data = row['value']
        text = data.get('text') or data.get('summary') or ''
        if row['kind'] in ('step','attempt'):
            action = data.get('action')
            text = {'memory': '翻找過去留下的記憶。', 'knowledge': '整理與這件事相關的理解。',
                    'task_search': '回顧做過的任務。', 'task_history': '接續閱讀一段舊任務的過程。',
                    'web_search': '尋找世界另一端的消息。', 'web_read': '讀了一段網路上的內容。',
                    'tool': '運用已學會的本領，向目標再走一步。', 'permission': '停下來，等待你的同意。',
                    'web_roam': '依你的邀請，去網路上走走。'}.get(action, '完成了一次整理。')
            if action in private_actions:
                text = '在成長與經歷之間，完成了一次整理。'
            text=language.text(text)
            if action not in private_actions and isinstance(data.get('request'),dict):
                query=data['request'].get('query')
                if isinstance(query,str) and query.strip(): text+=' '+language.text('這次想找的是「')+query[:600]+language.text('」。')
                result=data.get('result')
                if isinstance(result,dict) and result.get('error'): text+=' '+language.text('這一步未能完成，已保留進度。')
            if row['kind']=='attempt': text=language.text('正要著手：')+text
        elif row['kind'] in ('evidence','knowledge_gate'):
            text=language.text('補足知識' if row['kind']=='knowledge_gate' else '執行紀錄')
        events.append({'id': row['id'], 'kind': row['kind'], 'created_at': row['created_at'],
                       'text': text, 'role': data.get('role'), 'status': data.get('status'),
                       'brief':task_brief(data.get('brief')) if row['kind']=='task_brief' else None,
                       'subtasks': data.get('subtasks', []), 'note': data.get('note', '')})
    return {**{key: value[key] for key in ('total', 'offset', 'limit')},
            'task': task_summary(value['task']), 'events': events}


class UserViews:
    def __init__(self, runtime):
        self.r = runtime

    def emotion(self):
        r = self.r
        if not r.has_plugin('auren.emotion/1'):return {'installed':False,'performance':False,'flowing':False,'description':r.language.text(SYSTEM_TEXT['emotion'])}
        state, trauma = r.emotion.state(), r.emotion.trauma_state()
        return {'emotion': state.get('dominant_emotion') or '平靜',
                'flowing': not state['frozen'], 'performance': r.emotion.get_setting('performance', True),
                'reminder': r.emotion.get_setting('reminder', False),
                'research': r.emotion.research_state(),
                'trauma': {key: trauma[key] for key in ('enabled', 'frozen', 'phase', 'label', 'episodes')},
                'description': r.language.text(SYSTEM_TEXT['emotion'])}

    def persona(self):
        r = self.r
        persona = r.emotion.get_setting('persona', {})
        words = []
        for key, low, high in [('warmth', '習慣留一點安靜的空間', '說話帶著溫和的暖意'),
                                ('expressiveness', '把心意放在簡單的字句裡', '願意把心裡的波紋說得更清楚'),
                                ('curiosity', '循著熟悉的事慢慢理解', '容易被新的風景吸引')]:
            if key in persona:
                words.append(r.language.text(high if persona[key] >= .5 else low))
        shaping=r.personality_shaping.status()
        if shaping['available'] and persona.get('portrait',{}).get('summary'):words=[persona['portrait']['summary']]
        return {'identity': identity(r.emotion.identity()), 'growth_enabled': r.has_plugin('auren.growth/1') and r.emotion.get_setting('growth_enabled',False),
                'shaping':shaping,
                'expectation':r.emotion.personality_expectation()['goal'],
                'description': r.language.text(SYSTEM_TEXT['persona']), 'traits': words or [r.language.text('還沒有固定的樣子，正從第一次相遇開始累積。')],
                'emotion': self.emotion(), 'memories': r.memory.status().details.get('records', []),
                'tasks': r.permission.task_counts(),
                'history': [{'id': row['id'], 'at': row['at'], 'text': r.language.text('一段經歷，讓熟悉的樣子多了一點變化。')}
                            for row in r.emotion.profile_versions(r.agent_id)]}

    def statuses(self):
        r = self.r
        values = r.registry.statuses()
        if not r.has_plugin('auren.emotion/1'):values.pop('emotion',None)
        result = {key: {'ready': value['ready'], 'details': {}} for key, value in values.items()}
        
        if r.has_plugin('auren.emotion/1'):result['emotion']={'ready':r.emotion.health(),'details':self.emotion()}
        result['memory']['details'] = {'records': values['memory'].get('details', {}).get('records', [])}
        result['knowledge']['details'] = {key: values['knowledge'].get('details', {}).get(key, 0) for key in ('knowledge', 'tools')}
        result['learning']['details'] = {'roaming': {**values['learning'].get('details', {}).get('roaming', {}), 'automatic':r.roaming.schedule()['automatic'] if r.has_plugin('auren.roaming/1') else False}}
        result['permission']['details'] = {'tasks': values['permission'].get('details', {}).get('tasks', [])}
        return result

    def settings(self):
        r = self.r
        return {'effective_model': r.effective_model(), 'model': r.core.get_setting('model'),
                'model_mode': r.core.get_setting('model_mode', 'override' if r.core.get_setting('model') else 'inherit'),
                'external_model_consent': r.core.get_setting('external_model_consent', False),
                'growth_enabled': r.has_plugin('auren.growth/1') and r.emotion.get_setting('growth_enabled',False), 'emotion': self.emotion(),
                'upload_consent': r.security.get_setting('upload_consent', False),
                'approval_mode': r.core.approval_mode(), 'shaping':r.personality_shaping.status() if r.has_plugin('auren.personality-shaping/1') else {'available':False}, 'developer': {'enabled': r.core.get_setting('developer_mode', False)}}

    def panel(self, name):
        r = self.r
        if name == 'settings': return self.settings()
        if name == 'persona': return self.persona()
        if name == 'emotion': return self.emotion()
        if name == 'overview': return {'modules': self.statuses()}
        if name == 'chat': return {'personality_available':r.has_plugin('auren.personality/1'),'conversations': [{**row,'persona':identity(row['persona'])} for row in r.conversations()], **profiles(r.emotion.profiles()),'shaping':r.personality_shaping.status() if r.has_plugin('auren.personality-shaping/1') else {'available':False}}
        if name in ('diary', 'memory', 'user', 'todo'):
            layer = 'diary' if name == 'memory' else name
            return {'records': [{key: row[key] for key in ('id', 'layer', 'text', 'created_at', 'archived')} for row in r.memory.list_records(layer=layer)]}
        if name == 'learning':
            graph = r.knowledge.overview()
            return {'candidates': [{'id': row['id'], 'summary': row.get('summary') or row.get('content_summary', ''), 'status': row['status']}
                                   for row in r.learning.list_candidates()],
                    'knowledge': [{key: row.get(key) for key in ('id', 'content_summary', 'status')} for row in graph['knowledge']],
                    'tools': [{key: row.get(key) for key in ('id', 'name', 'capabilities', 'status')} for row in graph['tools']]}
        if name == 'roaming': return r.roaming.public_state()
        if name == 'tasks':
            result = r.permission.search_tasks()
            return {**result, 'tasks': [task_summary(task) for task in result['tasks']]}
        if name == 'permissions': return {'grants': r.permission.grants()}
        if name == 'rules': return {'rules': r.permission.rules()}
        if name == 'license': return r.license.status().dto()
        if name in ('thinking', 'curiosity', 'tools', 'developer', 'infra', 'security', 'upgrades', 'log'):
            return {'description': r.language.text('有些事情安靜地在幕後運作，讓每一次相遇都能安穩地延續。')}
        raise DomainError('未知頁面')
