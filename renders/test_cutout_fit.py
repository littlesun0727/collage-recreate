import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from workflow import asset_fit
from compositor import compose


class CutoutFitTests(unittest.TestCase):
    def test_compose_with_cutout_uses_contain(self):
        asset = {'member_keys': ['slot:large_cutout', 'overlay:arrow'], 'method': 'compose'}
        self.assertEqual(asset_fit(asset, {'slot:large_cutout'}), ('contain', True))

    def test_other_compose_can_stretch(self):
        asset = {'member_keys': ['overlay:arrow'], 'method': 'compose'}
        self.assertEqual(asset_fit(asset, {'slot:large_cutout'}), ('stretch', False))

    def test_layout_rejects_stretch_for_cutout(self):
        layout = {'canvas_size': [100, 100], 'layers': [{
            'key': 'person', 'layer_index': 0, 'box': [0, 0, 100, 100],
            'fit': 'stretch', 'contains_cutout': True,
        }]}
        with self.assertRaisesRegex(ValueError, 'preserve aspect ratio'):
            compose(layout)


if __name__ == '__main__':
    unittest.main()
