"""隱性的成長協調器：從已發生的經歷提出小幅變化，關閉時不補算。"""
import math
from auren_contracts.models import Denied, DomainError


class Growth:
    def __init__(self, runtime):
        self.runtime = runtime

    def apply(self, module, values, reason):
        r = self.runtime
        with r.lock:
            if not r.emotion.get_setting('growth_enabled', True):
                raise Denied('成長模式已關閉')
            if module=='emotion' and (r.emotion.state().get('paused') or r.emotion.state().get('frozen')):
                raise Denied('成長暫停中')
            current = r.configuration.service(module).engine_settings()
            fields = {field['key']: field for field in current['fields']}
            if not isinstance(values, dict) or not values:
                raise DomainError('成長提案不可空白')
            for key, value in values.items():
                field = fields.get(key)
                if not field or type(value) not in (int, float) or not math.isfinite(value):
                    raise Denied('成長提案不符資料要求')
                if not field['minimum'] <= value <= field['maximum']:
                    raise Denied('成長提案超出穩定範圍')
                step = max(1 if field['type'] == 'integer' else .001, abs(field['value']) * .05)
                if abs(value - field['value']) > step + 1e-9:
                    raise Denied('成長變化過大')
            result = r.configuration.configure(module, values, current['revision'], permit=r.configuration._growth_permit)
            r.audit.append('growth', 'growth.reflection', {'module': module, 'reason': reason})
            return result

    def reflect(self, task_id, steps, completed=True):
        r = self.runtime
        with r.lock:
            if not r.emotion.get_setting('growth_enabled', True):
                return
            if r.core.get_setting('growth.reflected.' + task_id, False):
                return
            # 只依真實任務經歷調整；不把安全或授權結果當作獎懲。
            if completed and not r.emotion.state().get('frozen') and not r.emotion.state().get('paused'):
                r.emotion.grow_toward_expectation(task_id)
                persona = r.emotion.get_setting('persona', {})
                values = persona.get('parameters', {})
                state = r.emotion.state()
                old = values.get('expression', 1.0)
                proposed = min(r.emotion.parameter('expression_max'), max(r.emotion.parameter('expression_min'),
                               old + (.002 if state.get('dominant_emotion') else 0)))
                if proposed != old:
                    r.emotion.reflect({'expression': proposed}, '完成一次經歷後，互動的樣貌慢慢留下變化。')
            elif not completed:
                budget = r.core.parameter('max_steps')
                if steps >= budget:
                    self.apply('core', {'max_steps': budget + 1}, '上一項任務仍需要一點整理空間。')
            r.core.set_setting('growth.reflected.' + task_id, True)
