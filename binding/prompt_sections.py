"""Read stage prompts from the single agent workflow document."""
import hashlib
from pathlib import Path

DOCUMENT = Path(__file__).resolve().parent/'binding.md'
TITLES = {'describe': '# 客户素材描述', 'match': '# 客户素材绑定', 'text': '# 不可辨认文案适配'}
# Migration from the byte-hashed describing.md (UTF-8 BOM and terminal CRLF).
# Accept it only while the canonical description rules remain exactly equivalent.
LEGACY_DESCRIPTION_HASH = '5001852fdf3c5d8bf40d71897330f14daad45aac6e0843c7514fbc33fc3fed73'
EQUIVALENT_DESCRIPTION_HASH = 'd3d6bd91cdf2b0f6f6460ec6278b5819ab66950a285e96f499fb508820236b36'


def read_stage(stage):
    if stage not in TITLES:
        raise ValueError(f'Unknown binding stage: {stage}')
    text = DOCUMENT.read_text(encoding='utf-8-sig')
    start, end = f'<!-- binding:{stage}:start -->\n', f'<!-- binding:{stage}:end -->'
    if text.count(start) != 1 or text.count(end) != 1:
        raise ValueError(f'Expected one complete {stage} section in binding.md')
    begin = text.index(start)+len(start)
    finish = text.index(end)
    if finish <= begin:
        raise ValueError(f'Empty or reversed {stage} section')
    return TITLES[stage]+'\n\n'+text[begin:finish]


def description_hash():
    return hashlib.sha256(read_stage('describe').encode('utf-8')).hexdigest()


def compatible_description_hashes():
    current = description_hash()
    return {current, LEGACY_DESCRIPTION_HASH} if current == EQUIVALENT_DESCRIPTION_HASH else {current}
