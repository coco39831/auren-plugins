"""同一套件發布人格建立、問答塑形、思路、期待與成長契約。"""
from .personality import Personality
from .growth import Growth
from .seed_interview import SeedInterview
from .personality_dialogue import PersonalityDialogue
from .personality_shaping import PersonalityShaping
from .portrait_reasoning import PortraitReasoning


def create(context,export_id):
    root=context.require('auren.kernel.application/1')
    if export_id=='personality':return Personality(root)
    if export_id=='seed-interview':return SeedInterview(root,root.default_model)
    return {'growth':Growth,'personality-dialogue':PersonalityDialogue,
            'personality-shaping':PersonalityShaping,'personality-reasoning':PortraitReasoning}[export_id](root)
