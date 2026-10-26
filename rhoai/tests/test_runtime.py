"""Runtime deployment contract with an isolated OpenShift command boundary."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]
IMAGE = "registry.example/runtime@sha256:" + "a" * 64
OVERRIDE = "registry.example/runtime@sha256:" + "b" * 64
VERSION_ANNOTATION = "opendatahub.io/runtime-version"


class RuntimeTests(unittest.TestCase):
    def run_deploy(self, *, override=None, template=None, fail=False, orchestrated=False,
                   current_namespace="fixture-lab", local_annotations=None):
        if template is None:
            template = {"objects": [{"kind": "ServingRuntime", "spec": {"containers": [
                {"name": "sidecar", "image": "sidecar:keep"},
                {"name": "kserve-container", "image": IMAGE}]}}]}
        with tempfile.TemporaryDirectory(prefix='lab, runtime ') as directory:
            folder = Path(directory)
            for name in ("02-deploy-runtime.sh", "02-serving-runtime.yaml", "00-run-lab.sh"):
                if (ROOT / name).exists():
                    shutil.copy2(ROOT / name, folder / name)
            (folder / "01-setup-project.sh").write_text(
                '#!/bin/bash\noc label namespace "$1" fixture=setup\n')
            (folder / "04-run-inference.sh").write_text("#!/bin/bash\nexit 0\n")
            (folder / "03-deploy-model.sh").write_text("#!/bin/bash\nexit 0\n")
            (folder / "template.json").write_text(json.dumps(template))
            # Return the local manifest with a sidecar first: only the named
            # model container may change, never the first container by position.
            (folder / "manifest.json").write_text(json.dumps({"kind": "ServingRuntime",
                "metadata": {"name": "vllm-cuda-runtime", "annotations": local_annotations or {}},
                "spec": {"containers": [
                {"name": "sidecar", "image": "sidecar:keep"},
                {"name": "kserve-container", "image": "old:pinned", "args": ["--port=8080"]}]}}))
            oc = folder / "oc"
            oc.write_text('''#!/usr/bin/env python3
import json, os, pathlib, sys
root = pathlib.Path(os.environ['FIXTURE_ROOT'])
args = sys.argv[1:]
namespace = args[args.index('-n') + 1] if '-n' in args else os.environ['FIXTURE_CURRENT_NAMESPACE']
with (root / 'calls.jsonl').open('a') as f: f.write(json.dumps(args) + '\\n')
if args[:2] == ['get', 'template']:
    if os.environ.get('FAIL_TEMPLATE') == '1': sys.exit('template lookup failed')
    print((root / 'template.json').read_text())
elif args[:2] == ['create', '--dry-run=client']:
    if ',' in args[args.index('-f') + 1]: sys.exit('oc treats comma-separated filenames as multiple paths')
    manifest = json.loads((root / 'manifest.json').read_text())
    manifest['metadata'].setdefault('namespace', namespace)
    print(json.dumps(manifest))
elif args[0] == 'apply':
    if args[-2:] == ['-f', '-']:
        manifest = json.load(sys.stdin)
        if manifest['metadata'].get('namespace', namespace) != namespace:
            sys.exit('manifest namespace does not match apply namespace')
        (root / 'applied.json').write_text(json.dumps(manifest))
    print('applied')
elif args[:2] == ['label', 'namespace']: print('labeled')
elif args[0] == 'wait': pass
else: sys.exit('Unexpected fixture command: ' + repr(args))
''')
            oc.chmod(0o700)
            env = {**os.environ, "FIXTURE_ROOT": str(folder), "FAIL_TEMPLATE": str(int(fail)),
                   "FIXTURE_CURRENT_NAMESPACE": current_namespace,
                   "PATH": str(folder) + os.pathsep + os.environ["PATH"]}
            for key in ("VLLM_IMAGE", "BASE_URL", "RESULTS_FILE", "EXPECTED_PROJECT_UID"):
                env.pop(key, None)
            if override is not None:
                env["VLLM_IMAGE"] = override
            before = hashlib.sha256((folder / "02-serving-runtime.yaml").read_bytes()).hexdigest()
            entry = ["00-run-lab.sh", "up", "fixture-lab"] if orchestrated else ["02-deploy-runtime.sh", "fixture-lab"]
            result = subprocess.run(["bash", str(folder / entry[0]), *entry[1:]],
                                    env=env, capture_output=True, text=True, timeout=10)
            applied = json.loads((folder / "applied.json").read_text()) if (folder / "applied.json").exists() else None
            calls = [json.loads(line) for line in (folder / "calls.jsonl").read_text().splitlines()] if (folder / "calls.jsonl").exists() else []
            self.assertEqual(before, hashlib.sha256((folder / "02-serving-runtime.yaml").read_bytes()).hexdigest())
            return result, applied, calls

    def test_installed_image_changes_only_named_container(self):
        result, applied, calls = self.run_deploy()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(applied['spec']['containers'], [
            {'name': 'sidecar', 'image': 'sidecar:keep'},
            {'name': 'kserve-container', 'image': IMAGE, 'args': ['--port=8080']}])
        self.assertIn(['apply', '-n', 'fixture-lab', '-f', '-'], calls)

    def test_explicit_override_skips_template_lookup(self):
        result, applied, calls = self.run_deploy(override=OVERRIDE, fail=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(applied['spec']['containers'][1]['image'], OVERRIDE)
        self.assertFalse(any(c[:2] == ['get', 'template'] for c in calls))

    def test_failed_template_lookup_never_applies(self):
        result, applied, calls = self.run_deploy(fail=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('template lookup failed', result.stderr)
        self.assertIsNone(applied)
        self.assertFalse(any(c[0] == 'apply' for c in calls))

    def test_missing_or_ambiguous_runtime_never_applies(self):
        for objects in ([], [{'kind': 'ConfigMap'}], [
            {'kind': 'ServingRuntime', 'spec': {'containers': [
                {'name': 'kserve-container', 'image': IMAGE},
                {'name': 'kserve-container', 'image': OVERRIDE}]}}]):
            with self.subTest(objects=objects):
                result, applied, _ = self.run_deploy(template={'objects': objects})
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('exactly one', result.stderr)
                self.assertIsNone(applied)

    def test_whitespace_image_is_rejected(self):
        result, applied, _ = self.run_deploy(override='bad image')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('image', result.stderr.lower())
        self.assertIsNone(applied)

    def test_manual_and_orchestrated_deployment_match(self):
        manual, first, _ = self.run_deploy()
        automated, second, _ = self.run_deploy(orchestrated=True)
        self.assertEqual(manual.returncode, 0, manual.stderr)
        self.assertEqual(automated.returncode, 0, automated.stderr)
        self.assertEqual(first, second)

    def test_target_namespace_is_materialized_when_current_namespace_differs(self):
        for orchestrated in (False, True):
            with self.subTest(orchestrated=orchestrated):
                result, applied, calls = self.run_deploy(
                    current_namespace="fixture-current", orchestrated=orchestrated)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(applied['metadata']['namespace'], 'fixture-lab')
                self.assertEqual(applied['spec']['containers'][1]['image'], IMAGE)
                materializations = [c for c in calls if c[:2] == ['create', '--dry-run=client']]
                self.assertEqual(len(materializations), 1)
                self.assertIn('-n', materializations[0])
                self.assertEqual(materializations[0][materializations[0].index('-n') + 1], 'fixture-lab')

    def assert_no_runtime_apply(self, result, applied, calls, orchestrated):
        # Count attempted applies, even when the fixture would reject them.
        # Earlier orchestrator setup mutations are allowed by this contract.
        runtime_applies = [c for c in calls if c[0] == 'apply' and c[-2:] == ['-f', '-']]
        self.assertEqual(runtime_applies, [])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('image', result.stderr.lower())
        self.assertNotIn('Traceback', result.stderr)
        self.assertIsNone(applied)
        if orchestrated:
            self.assertIn(['label', 'namespace', 'fixture-lab', 'fixture=setup'], calls)

    def test_invalid_installed_image_types_never_apply_in_either_entrypoint(self):
        for image in (None, 0, 123, False, [], {}, '', ' ', IMAGE + '\n', '\x00',
                      'registry.example/run\x00time:fixture'):
            for orchestrated in (False, True):
                with self.subTest(image=image, orchestrated=orchestrated):
                    template = {'objects': [{'kind': 'ServingRuntime', 'spec': {'containers': [
                        {'name': 'kserve-container', 'image': image}]}}]}
                    result, applied, calls = self.run_deploy(template=template, orchestrated=orchestrated)
                    self.assert_no_runtime_apply(result, applied, calls, orchestrated)
                    self.assertNotIn('ignored null byte', result.stderr)
                    self.assertFalse(any(c[:2] == ['create', '--dry-run=client'] for c in calls))

    def test_unresolved_image_tokens_never_apply_from_template_or_override(self):
        for image in ('${RUNTIME_IMAGE}', 'registry.example/runtime:${TAG}',
                      '$RUNTIME_IMAGE', '{{RUNTIME_IMAGE}}', 'registry.example/runtime:<tag>'):
            for installed in (False, True):
                for orchestrated in (False, True):
                    with self.subTest(image=image, installed=installed, orchestrated=orchestrated):
                        template = {'objects': [{'kind': 'ServingRuntime', 'spec': {'containers': [
                            {'name': 'kserve-container', 'image': image}]}}]}
                        options = {'template': template} if installed else {'override': image}
                        result, applied, calls = self.run_deploy(orchestrated=orchestrated, **options)
                        self.assert_no_runtime_apply(result, applied, calls, orchestrated)

    def test_inference_service_is_dashboard_visible_without_changing_predictor(self):
        service = yaml.safe_load((ROOT / "03-inference-service.yaml").read_text())
        self.assertEqual(service["metadata"].get("labels", {}).get("opendatahub.io/dashboard"), "true")
        self.assertEqual(service["metadata"]["annotations"]["serving.kserve.io/deploymentMode"], "RawDeployment")
        self.assertEqual(service["spec"]["predictor"], {
            "minReplicas": 1, "maxReplicas": 1,
            "tolerations": [{"key": "nvidia.com/gpu", "operator": "Exists", "effect": "NoSchedule"}],
            "model": {"runtime": "vllm-cuda-runtime", "modelFormat": {"name": "vLLM"},
                      "storageUri": "pvc://model-pvc/",
                      "args": ["--dtype=auto", "--max-model-len=4096", "--enforce-eager", "--enable-prefix-caching"],
                      "resources": {"requests": {"cpu": "4", "memory": "8Gi", "nvidia.com/gpu": "1"},
                                    "limits": {"cpu": "8", "memory": "16Gi", "nvidia.com/gpu": "1"}}}})

    def test_runtime_identifies_vllm_at_the_predictor_annotation_level(self):
        runtime = yaml.safe_load((ROOT / "02-serving-runtime.yaml").read_text())
        self.assertEqual(runtime["spec"]["annotations"].get("opendatahub.io/kserve-runtime"), "vllm")
        self.assertNotIn(VERSION_ANNOTATION, runtime["metadata"].get("annotations", {}))

    def test_runtime_has_dashboard_template_display_name(self):
        runtime = yaml.safe_load((ROOT / "02-serving-runtime.yaml").read_text())
        annotations = runtime["metadata"]["annotations"]
        self.assertEqual(annotations.get("opendatahub.io/template-display-name"),
                         "vLLM NVIDIA GPU ServingRuntime for KServe")
        self.assertEqual(annotations["opendatahub.io/template-display-name"],
                         annotations["openshift.io/display-name"])
        # This lab customizes the runtime spec; do not claim an unmodified
        # installed template identity or infer a runtime package version.
        self.assertNotIn("opendatahub.io/template-name", annotations)
        self.assertNotIn(VERSION_ANNOTATION, annotations)

    def versioned_template(self, version):
        return {"objects": [
            {"kind": "ConfigMap", "metadata": {"annotations": {VERSION_ANNOTATION: "not-a-runtime"}}},
            {"kind": "ServingRuntime", "metadata": {"annotations": {VERSION_ANNOTATION: "not-selected"}},
             "spec": {"containers": [{"name": "sidecar", "image": "sidecar:keep"}]}},
            {"kind": "ServingRuntime", "metadata": {"annotations": {VERSION_ANNOTATION: version}},
             "spec": {"containers": [{"name": "sidecar", "image": "sidecar:keep"},
                                     {"name": "kserve-container", "image": IMAGE}]}}]}

    def test_selected_installed_runtime_version_is_propagated_in_both_entrypoints(self):
        for version in ("v9.8.7-fixture", "1.2.3+fixture"):
            for orchestrated in (False, True):
                with self.subTest(version=version, orchestrated=orchestrated):
                    result, applied, _ = self.run_deploy(
                        template=self.versioned_template(version), orchestrated=orchestrated,
                        local_annotations={VERSION_ANNOTATION: "stale", "fixture.example/keep": "unchanged"})
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(applied["metadata"]["annotations"], {
                        VERSION_ANNOTATION: version, "fixture.example/keep": "unchanged"})
                    self.assertEqual(applied["spec"]["containers"][1]["image"], IMAGE)

    def test_missing_installed_version_does_not_keep_or_infer_a_version(self):
        for orchestrated in (False, True):
            with self.subTest(orchestrated=orchestrated):
                template = self.versioned_template("discard")
                del template["objects"][-1]["metadata"]["annotations"][VERSION_ANNOTATION]
                result, applied, _ = self.run_deploy(
                    template=template, orchestrated=orchestrated,
                    local_annotations={VERSION_ANNOTATION: "stale", "fixture.example/keep": "unchanged"})
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(applied["metadata"]["annotations"], {"fixture.example/keep": "unchanged"})

    def test_explicit_image_removes_stale_version_without_template_lookup(self):
        for orchestrated in (False, True):
            with self.subTest(orchestrated=orchestrated):
                result, applied, calls = self.run_deploy(
                    override=OVERRIDE, template=self.versioned_template("v9.8.7-fixture"), fail=True,
                    orchestrated=orchestrated,
                    local_annotations={VERSION_ANNOTATION: "stale", "fixture.example/keep": "unchanged"})
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(applied["metadata"]["annotations"], {"fixture.example/keep": "unchanged"})
                self.assertEqual(applied["spec"]["containers"][1]["image"], OVERRIDE)
                self.assertFalse(any(c[:2] == ["get", "template"] for c in calls))

    def test_invalid_installed_versions_never_apply_in_either_entrypoint(self):
        invalid_versions = (None, 0, 123, False, [], {}, "", " ", "v1\n", "v1\tv2", "v1\u00a0v2",
                            "${VERSION}", "$VERSION", "$(VERSION)", "{{VERSION}}", "<version>",
                            "\x00", "v1\x00.2")
        for version in invalid_versions:
            for orchestrated in (False, True):
                with self.subTest(version=version, orchestrated=orchestrated):
                    result, applied, calls = self.run_deploy(
                        template=self.versioned_template(version), orchestrated=orchestrated)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("runtime-version", result.stderr)
                    self.assertNotIn("Traceback", result.stderr)
                    self.assertNotIn("ignored null byte", result.stderr)
                    self.assertIsNone(applied)
                    self.assertFalse(any(c[0] == "apply" and c[-2:] == ["-f", "-"] for c in calls))
                    self.assertFalse(any(c[:2] == ["create", "--dry-run=client"] for c in calls))


if __name__ == '__main__':
    unittest.main()
