import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from build_v2_core.common import BuildError, sha
from build_v2_core.store import atomic, load
from build_v2_core.workflow import review, context_id, summary
from build_v2_core.review_budget import budget, ensure_remaining
from build_v2_core.review_selection import signatures, select_review


def object_(key, box):
    return {'key': key, 'member_keys': [key], 'target_box': box,
            'content_scope': {'allowed_text': []}, 'plan': {'method': 'draw'}}


class ReviewFlow(unittest.TestCase):
    def fixture(self, root):
        (root / 'evidence.png').write_bytes(b'checked evidence')
        atomic(root / 'delivery.json', {'unresolved': []})
        objects = [object_('a', [0, 0, 20, 20]), object_('b', [10, 10, 30, 30]),
                   object_('c', [60, 60, 80, 80])]
        row = {'status': 'awaiting_visual', 'review': None,
               'package': {'owned_keys': ['a', 'b', 'c'], 'member_keys': ['a', 'b', 'c'], 'objects': objects},
               'active': {'id': 'first', 'delivery': 'delivery.json',
                          'outputs': [{'key': k, 'sha256': 'pixels-' + k} for k in 'abc']}}
        state = {'schema_version': 'build-state-v2', 'revision': 'revision', 'engine': 'engine', 'input': {},
                 'limits': {'max_corrections': 2}, 'events': [], 'tasks': {'t': row}, 'sequence': 0}
        self.save_preview(root, state)
        return state

    def save_preview(self, root, state):
        row = state['tasks']['t']
        row['preview'] = {'context_id': context_id(state), 'evidence': {'evidence.png': sha(root / 'evidence.png')}}
        atomic(root / 'state.json', state)

    def receipt(self, state):
        row = state['tasks']['t']
        return {'schema_version': 'build-review-v2', 'task_id': 't', 'submission_id': row['active']['id'],
                'context_id': context_id(state), 'reviewer': 'test-fixture-not-visual-acceptance',
                'evidence': row['preview']['evidence'], 'elapsed_seconds': 1,
                'reviews': [{'key': k, 'status': 'needs_changes' if k == 'a' else 'passed',
                             'issues': ['wrong contour'] if k == 'a' else [],
                             'observed_text': '', 'unexpected_content': []} for k in 'abc'],
                'combination_issues': []}

    def test_two_corrections_defer_and_receipt_cannot_bypass(self):
        with tempfile.TemporaryDirectory() as tmp, patch('build_v2_core.workflow.refresh'), patch('build_v2_core.workflow.export'):
            root = Path(tmp)
            state = self.fixture(root)
            for index, expected in enumerate(['needs_changes', 'needs_changes', 'deferred']):
                state['tasks']['t']['active']['id'] = str(index)
                state['tasks']['t']['status'] = 'awaiting_visual'
                state['tasks']['t']['review'] = None
                self.save_preview(root, state)
                receipt = self.receipt(state)
                atomic(root / 'review.json', receipt)
                self.assertEqual(review(root, 't', root / 'review.json')['status'], expected)
                state = load(root)
                self.assertEqual(budget(state, 't')['completed_reviews'], index + 1)
            self.assertIn('renders', summary(state)['next_actions']['t'])
            self.assertTrue(review(root, 't', root / 'review.json')['reused'])
            with self.assertRaises(BuildError):
                ensure_remaining(state, 't')
            # Imported report for another submission cannot reset exhausted rounds.
            state['tasks']['t']['active']['id'] = 'fourth'
            state['tasks']['t']['status'] = 'awaiting_visual'
            state['tasks']['t']['review'] = None
            self.save_preview(root, state)
            atomic(root / 'review.json', self.receipt(state))
            with self.assertRaisesRegex(BuildError, 'correction limit'):
                review(root, 't', root / 'review.json')

    def test_failure_does_not_spend_visual_round_and_regroup_keeps_budget(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = self.fixture(Path(tmp))
            state['events'] = [{'event': 'model_review_failed', 'details': {'task': 't'}, 'sequence': i} for i in range(4)]
            self.assertEqual(budget(state, 't')['completed_reviews'], 0)
            state['events'].append({'event': 'visual_review', 'details': {'task': 'old-task',
                       'member_keys': ['a'], 'submission_id': 'old'}, 'sequence': 5})
            self.assertEqual(budget(state, 't')['completed_reviews'], 1)

    def test_changed_unit_and_direct_neighbor_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = self.fixture(Path(tmp)); row = state['tasks']['t']
            prior = self.receipt(state)
            for r in prior['reviews']: r.update(status='passed', issues=[])
            row['review_cache'] = {'receipt': prior, 'signatures': signatures(state, row)}
            row['active']['outputs'][0]['sha256'] = 'changed-pixels'
            required, reused = select_review(state, row)
            self.assertEqual(required, ['a', 'b'])
            self.assertEqual(list(reused), ['c'])
            state['engine'] = 'changed-engine'
            self.assertEqual(select_review(state, row)[0], ['a', 'b', 'c'])

    def test_zero_corrections_stops_after_first_failure(self):
        with tempfile.TemporaryDirectory() as tmp, patch('build_v2_core.workflow.refresh'), patch('build_v2_core.workflow.export'):
            root = Path(tmp); state = self.fixture(root)
            state['limits']['max_corrections'] = 0
            self.save_preview(root, state)
            atomic(root / 'review.json', self.receipt(state))
            self.assertEqual(review(root, 't', root / 'review.json')['status'], 'deferred')


if __name__ == '__main__':
    unittest.main()
