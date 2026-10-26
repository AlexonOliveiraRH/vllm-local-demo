"""Placement decisions for heterogeneous GPUs and shared or zonal storage."""
import copy
import importlib.util
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


def node(name, major=8, minor=9, zone=None, taints=None, count=1):
    labels = {'kubernetes.io/hostname': name, 'nvidia.com/gpu.present': 'true',
              'nvidia.com/gpu.compute.major': str(major), 'nvidia.com/gpu.compute.minor': str(minor),
              'nvidia.com/gpu.product': 'test-modern-gpu' if major >= 8 else 'test-older-gpu'}
    if zone:
        labels['topology.kubernetes.io/zone'] = zone
    return {'metadata': {'name': name, 'labels': labels}, 'spec': {'taints': taints or []},
            'status': {'allocatable': {'nvidia.com/gpu': str(count)},
                       'conditions': [{'type': 'Ready', 'status': 'True'}]}}


def consumer(name, count=1, phase='Running', project='another-team', init=None):
    return {'metadata': {'name': 'consumer', 'namespace': project},
            'spec': {'nodeName': name, 'containers': [{'resources': {'limits': {'nvidia.com/gpu': str(count)}}}],
                     'initContainers': init or []}, 'status': {'phase': phase}}


class PlacementTests(unittest.TestCase):
    def setUp(self):
        path = ROOT / 'placement.py'
        self.assertTrue(path.exists(), 'GPU/storage placement resolver is missing')
        spec = importlib.util.spec_from_file_location('lab_placement', path)
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        self.sc = {'metadata': {'name': 'example-csi'}, 'volumeBindingMode': 'WaitForFirstConsumer'}

    def plan(self, nodes=None, pods=None, env=None, storage=None, required_gpus=1):
        return self.module.make_plan(nodes or [node('gpu-a')], pods or [], storage or self.sc, env or {}, 'test-lab', required_gpus)

    def test_legacy_gpu_is_excluded_even_when_listed_first(self):
        result = self.plan([node('legacy', 6, 1), node('modern')])
        self.assertEqual(result['nodeSelector'], {'kubernetes.io/hostname': 'modern'})

    def test_unknown_or_unsupported_compute_capability_is_rejected(self):
        for gpu in [node('legacy', 7, 0), node('unknown')]:
            if gpu['metadata']['name'] == 'unknown':
                gpu['metadata']['labels'].pop('nvidia.com/gpu.compute.major')
            with self.subTest(gpu=gpu), self.assertRaisesRegex(ValueError, 'compatible|capability'):
                self.plan([gpu])

    def test_custom_taint_requires_explicit_matching_toleration(self):
        gpu = node('custom', taints=[{'key': 'example.org/accelerator', 'effect': 'NoSchedule'}])
        with self.assertRaisesRegex(ValueError, 'toleration|compatible'):
            self.plan([gpu])
        env = {'GPU_TOLERATIONS': json.dumps([{'key': 'example.org/accelerator', 'operator': 'Exists', 'effect': 'NoSchedule'}])}
        result = self.plan([gpu], env=env)
        self.assertEqual(result['tolerations'][0]['key'], 'example.org/accelerator')

    def test_unrelated_dedicated_node_is_not_silently_tolerated(self):
        with self.assertRaises(ValueError):
            self.plan([node('dedicated', taints=[{'key': 'team', 'value': 'other', 'effect': 'NoSchedule'}])])

    def test_full_node_is_skipped_and_completed_pods_do_not_reserve_gpu(self):
        result = self.plan([node('full'), node('free')], [consumer('full'), consumer('free', phase='Succeeded')])
        self.assertEqual(result['nodeSelector']['kubernetes.io/hostname'], 'free')

    def test_restartable_init_gpu_is_counted(self):
        init = [{'restartPolicy': 'Always', 'resources': {'limits': {'nvidia.com/gpu': '1'}}}]
        with self.assertRaisesRegex(ValueError, 'capacity|compatible'):
            self.plan([node('gpu-a', count=2)], [consumer('gpu-a', init=init)])

    def test_selector_and_storage_zone_both_constrain_selection(self):
        env = {'GPU_NODE_SELECTOR': '{"nvidia.com/gpu.product":"test-modern-gpu"}', 'STORAGE_ZONE': 'zone-b'}
        result = self.plan([node('a', zone='zone-a'), node('b', zone='zone-b')], env=env)
        self.assertEqual(result['nodeSelector']['kubernetes.io/hostname'], 'b')

    def test_immediate_binding_requires_shared_storage_confirmation(self):
        self.sc['volumeBindingMode'] = 'Immediate'
        with self.assertRaisesRegex(ValueError, 'STORAGE_TOPOLOGY=shared'):
            self.plan()
        result = self.plan(env={'STORAGE_TOPOLOGY': 'shared'})
        self.assertEqual(result['storageClass'], 'example-csi')

    def test_unready_or_cordoned_node_cannot_be_selected(self):
        a, b = node('not-ready'), node('cordoned')
        a['status']['conditions'][0]['status'] = 'False'
        b['spec']['unschedulable'] = True
        with self.assertRaises(ValueError):
            self.plan([a,b])

    def test_storage_allowed_topologies_exclude_otherwise_available_gpu(self):
        self.sc['allowedTopologies'] = [{'matchLabelExpressions': [
            {'key': 'topology.kubernetes.io/zone', 'values': ['zone-b']}]}]
        result = self.plan([node('a', zone='zone-a'), node('b', zone='zone-b')])
        self.assertEqual(result['nodeSelector']['kubernetes.io/hostname'], 'b')

    def test_gpu_request_can_require_more_than_one_available_device(self):
        result = self.plan([node('one', count=1), node('two', count=2)], required_gpus=2)
        self.assertEqual(result['nodeSelector']['kubernetes.io/hostname'], 'two')

    def test_model_resource_requests_must_match_limits(self):
        model = {'spec': {'predictor': {'model': {'resources': {
            'requests': {'nvidia.com/gpu': '1'}, 'limits': {'nvidia.com/gpu': '2'}}}}}}
        with self.assertRaises(ValueError):
            self.module.required_gpus(model)

    def test_invalid_configuration_fails_before_any_manifest_is_rendered(self):
        for env in ({'GPU_NODE_SELECTOR': 'not-json'}, {'GPU_NODE_SELECTOR': '{"x":42}'},
                    {'GPU_TOLERATIONS': '[{"operator":"Exists"}]'},
                    {'GPU_TOLERATIONS': '[{"key":"team","operator":"Wrong"}]'},
                    {'STORAGE_TOPOLOGY': 'guess'}, {'GPU_NODE_SELECTOR': '{"x":"a\\nb"}'}):
            with self.subTest(env=env), self.assertRaises(ValueError):
                self.plan(env=env)

    def test_predictor_uses_persisted_placement_and_preserves_inference_settings(self):
        manifest = {'spec': {'predictor': {'model': {'args': ['--enable-prefix-caching'],
                    'resources': {'limits': {'nvidia.com/gpu': '1'}}}, 'minReplicas': 1}}}
        before = copy.deepcopy(manifest['spec']['predictor']['model'])
        updated = self.module.render_predictor(manifest, self.plan(), 'test-lab')
        self.assertEqual(updated['spec']['predictor']['model'], before)
        self.assertEqual(updated['spec']['predictor']['nodeSelector'], {'kubernetes.io/hostname': 'gpu-a'})
        self.assertEqual(updated['metadata']['namespace'], 'test-lab')


if __name__ == '__main__':
    unittest.main()
