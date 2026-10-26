"""The manual deployment entry point must apply the PVC's resolved placement."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class ModelDeployTests(unittest.TestCase):
    def deploy(self, placement, storage_class='example-csi'):
        with tempfile.TemporaryDirectory(prefix='lab, model ') as directory:
            root = Path(directory)
            for name in ('03-deploy-model.sh', '03-inference-service.yaml', 'placement.py'):
                shutil.copy2(ROOT / name, root / name)
            pvc = {'metadata': {'annotations': {}}, 'spec': {'storageClassName': storage_class}}
            if placement is not None:
                pvc['metadata']['annotations']['demo.vllm/placement'] = json.dumps(placement)
            (root / 'pvc.json').write_text(json.dumps(pvc))
            oc = root / 'oc'
            oc.write_text('''#!/usr/bin/env python3
import json, os, pathlib, sys, yaml
root = pathlib.Path(os.environ['FIXTURE_ROOT'])
args = sys.argv[1:]
if '-n' not in args or args[args.index('-n')+1] != 'fixture-lab':
    sys.exit('Wrong namespace')
if args[:2] == ['create', '--dry-run=client']:
    if ',' in args[args.index('-f')+1]: sys.exit('oc treats comma-separated filenames as multiple paths')
    print(json.dumps(yaml.safe_load((root/'03-inference-service.yaml').read_text())))
elif args[:3] == ['get', 'pvc', 'model-pvc']:
    print((root/'pvc.json').read_text())
elif args[0] == 'apply' and args[-2:] == ['-f', '-']:
    (root/'applied.json').write_text(json.dumps(json.load(sys.stdin)))
else:
    sys.exit('Unexpected fixture command: '+repr(args))
''')
            oc.chmod(0o700)
            env = {**os.environ, 'PATH': str(root)+os.pathsep+os.environ['PATH'], 'FIXTURE_ROOT': str(root)}
            result = subprocess.run(['bash', str(root/'03-deploy-model.sh'), 'fixture-lab'], env=env,
                                    capture_output=True, text=True, timeout=10)
            applied = json.loads((root/'applied.json').read_text()) if (root/'applied.json').exists() else None
            self.assertNotIn('Unexpected fixture', result.stderr)
            return result, applied

    def saved(self):
        return {'version': 1, 'storageClass': 'example-csi', 'storageTopology': 'shared',
                'nodeSelector': {'kubernetes.io/hostname': 'selected-gpu'},
                'tolerations': [{'key': 'example.org/gpu', 'operator': 'Exists', 'effect': 'NoSchedule'}]}

    def test_saved_placement_reaches_the_applied_predictor(self):
        result, applied = self.deploy(self.saved())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(applied['metadata']['namespace'], 'fixture-lab')
        predictor = applied['spec']['predictor']
        self.assertEqual(predictor['nodeSelector'], {'kubernetes.io/hostname': 'selected-gpu'})
        self.assertEqual(predictor['tolerations'], [{'key': 'example.org/gpu', 'operator': 'Exists', 'effect': 'NoSchedule'}])
        self.assertEqual(predictor['model']['storageUri'], 'pvc://model-pvc/')
        self.assertIn('--enable-prefix-caching', predictor['model']['args'])

    def test_missing_invalid_or_mismatched_placement_never_applies(self):
        for placement, sc in ((None, 'example-csi'), ({}, 'example-csi'), (self.saved(), 'different-csi')):
            with self.subTest(placement=placement, sc=sc):
                result, applied = self.deploy(placement, sc)
                self.assertNotEqual(result.returncode, 0)
                self.assertIsNone(applied)
                self.assertNotIn('Traceback', result.stderr)


if __name__ == '__main__':
    unittest.main()
