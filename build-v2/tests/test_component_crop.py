import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from build_v2_core.common import BuildError
from build_v2_core.tools import component_entry


class ComponentCropTests(unittest.TestCase):
    def setUp(self):
        self.unit = {
            'key': 'unit-example',
            'member_keys': ['overlay:coffee', 'text:caption', 'overlay:lemon'],
            'members': [
                {'key': 'overlay:coffee', 'type': 'overlay', 'draft': {},
                 'reference_box': [700, 100, 900, 250], 'target_box': [700, 100, 900, 250]},
                {'key': 'text:caption', 'type': 'text', 'draft': {'default_text': 'Hello'},
                 'reference_box': [100, 300, 500, 360], 'target_box': [100, 300, 500, 360]},
                {'key': 'overlay:lemon', 'type': 'overlay', 'draft': {},
                 'reference_box': [20, 400, 160, 530], 'target_box': [20, 400, 160, 530]},
            ],
            'reference_box': [20, 100, 900, 530],
            'target_box': [20, 100, 900, 530],
            'size': [880, 430],
            'plan': {'method': 'compose'},
            'content_scope': {'allowed_text': ['Hello'], 'keep': 'whole unit', 'exclude_keys': []},
        }

    def test_single_member_uses_own_crop_and_size(self):
        original = copy.deepcopy(self.unit)
        result = component_entry(self.unit, ['overlay:coffee'])
        self.assertEqual(result['reference_box'], [700, 100, 900, 250])
        self.assertEqual(result['target_box'], [700, 100, 900, 250])
        self.assertEqual(result['size'], [200, 150])
        self.assertEqual(result['content_scope']['allowed_text'], [])
        self.assertEqual([m['key'] for m in result['members']], ['overlay:coffee'])
        self.assertEqual(self.unit, original)

    def test_multiple_members_use_their_union(self):
        result = component_entry(self.unit, ['text:caption', 'overlay:lemon'])
        self.assertEqual(result['reference_box'], [20, 300, 500, 530])
        self.assertEqual(result['size'], [480, 230])
        self.assertEqual(result['content_scope']['allowed_text'], ['Hello'])

    def test_nonmember_is_rejected(self):
        with self.assertRaises(BuildError):
            component_entry(self.unit, ['overlay:missing'])


if __name__ == '__main__':
    unittest.main()
