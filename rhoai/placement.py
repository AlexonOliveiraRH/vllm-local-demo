#!/usr/bin/env python3
"""Resolve GPU placement without embedding a cluster, storage class, or GPU model."""
import copy
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ANNOTATION = 'demo.vllm/placement'
DEFAULT_TOLERATIONS = [{'key': 'nvidia.com/gpu', 'operator': 'Exists', 'effect': 'NoSchedule'}]
LABEL = re.compile(r'^[A-Za-z0-9]([A-Za-z0-9_.-]{0,61}[A-Za-z0-9])?$')


def label_key(value):
    if not isinstance(value, str):
        return False
    parts = value.split('/')
    return (len(parts) <= 2 and bool(LABEL.fullmatch(parts[-1])) and
            (len(parts) == 1 or (len(parts[0]) <= 253 and all(
                re.fullmatch(r'[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?', p) for p in parts[0].split('.')))))


def selector(value):
    if not isinstance(value, dict) or any(not label_key(k) or not isinstance(v, str) or
            (v != '' and not LABEL.fullmatch(v)) for k, v in value.items()):
        raise ValueError('GPU_NODE_SELECTOR must be a JSON object of valid Kubernetes label strings')
    return value


def tolerations(value):
    if not isinstance(value, list):
        raise ValueError('GPU_TOLERATIONS must be a JSON array')
    for t in value:
        if (not isinstance(t, dict) or set(t) - {'key', 'operator', 'value', 'effect'} or
                not label_key(t.get('key')) or t.get('operator', 'Equal') not in ('Equal', 'Exists') or
                t.get('effect') not in ('NoSchedule', 'NoExecute', 'PreferNoSchedule') or
                not isinstance(t.get('value', ''), str) or
                (t.get('value', '') != '' and not LABEL.fullmatch(t['value'])) or
                (t.get('operator') == 'Exists' and t.get('value', '') != '')):
            raise ValueError('GPU_TOLERATIONS requires explicit valid keys, operators, and effects; wildcard tolerations are not accepted')
    return value


def gpu_request(container):
    resources = container.get('resources', {})
    return int(resources.get('requests', {}).get('nvidia.com/gpu', resources.get('limits', {}).get('nvidia.com/gpu', 0)))


def pod_gpu(pod):
    # Account for restartable init containers as well as sequential init maxima.
    sidecars = 0
    peak = 0
    for c in pod['spec'].get('initContainers', []):
        amount = gpu_request(c)
        if c.get('restartPolicy') == 'Always':
            sidecars += amount
            peak = max(peak, sidecars)
        else:
            peak = max(peak, sidecars + amount)
    return max(peak, sidecars + sum(gpu_request(c) for c in pod['spec'].get('containers', [])))


def tolerates(taint, configured):
    return any(t['key'] == taint['key'] and t['effect'] == taint['effect'] and
               (t.get('operator', 'Equal') == 'Exists' or t.get('value', '') == taint.get('value', ''))
               for t in configured)


def required_gpus(manifest):
    resources = manifest['spec']['predictor']['model']['resources']
    request = resources.get('requests', {}).get('nvidia.com/gpu', resources.get('limits', {}).get('nvidia.com/gpu'))
    limit = resources.get('limits', {}).get('nvidia.com/gpu')
    if (not re.fullmatch(r'[1-9][0-9]*', str(request)) or
            not re.fullmatch(r'[1-9][0-9]*', str(limit)) or int(request) != int(limit)):
        raise ValueError('Predictor GPU requests and limits must be equal positive integers')
    return int(request)


def allowed_topology(labels, storage):
    terms = storage.get('allowedTopologies')
    if not terms:
        return True
    return any(term.get('matchLabelExpressions') and all(
        labels.get(e['key']) in e.get('values', []) for e in term['matchLabelExpressions']) for term in terms)


def make_plan(nodes, pods, storage, env, project, required_gpus=1):
    selected = selector(json.loads(env.get('GPU_NODE_SELECTOR', '{}')))
    configured = tolerations(json.loads(env.get('GPU_TOLERATIONS', json.dumps(DEFAULT_TOLERATIONS))))
    topology = env.get('STORAGE_TOPOLOGY', 'auto')
    if topology not in ('auto', 'shared'):
        raise ValueError('STORAGE_TOPOLOGY must be auto or shared')
    mode = storage.get('volumeBindingMode', 'Immediate')
    if mode == 'Immediate' and topology != 'shared':
        raise ValueError('Immediate storage requires STORAGE_TOPOLOGY=shared after confirming that volumes are accessible from every selected GPU node; otherwise choose WaitForFirstConsumer')
    if mode not in ('Immediate', 'WaitForFirstConsumer'):
        raise ValueError('Unsupported storage binding mode')
    used = {}
    for p in pods:
        if p.get('status', {}).get('phase') in ('Succeeded', 'Failed'):
            continue
        # Re-running setup may reuse this project's existing predictor allocation.
        if (p['metadata'].get('namespace') == project and
                p['metadata'].get('labels', {}).get('serving.kserve.io/inferenceservice') == 'qwen25-3b'):
            continue
        name = p['spec'].get('nodeName')
        used[name] = used.get(name, 0) + pod_gpu(p)
    reasons = []
    for n in sorted(nodes, key=lambda x: x['metadata']['name']):
        name = n['metadata']['name']
        labels = n['metadata'].get('labels', {})
        if labels.get('nvidia.com/gpu.present') != 'true':
            continue
        if any(labels.get(k) != v for k,v in selected.items()):
            continue
        if env.get('STORAGE_ZONE') and labels.get('topology.kubernetes.io/zone') != env['STORAGE_ZONE']:
            continue
        if not allowed_topology(labels, storage):
            continue
        reason = None
        try:
            capability = (int(labels['nvidia.com/gpu.compute.major']), int(labels['nvidia.com/gpu.compute.minor']))
        except (KeyError, ValueError):
            capability = (0, 0)
        if capability < (7, 5):
            reason = 'missing or unsupported compute capability (requires 7.5+)'
        elif n['spec'].get('unschedulable') or not any(c['type'] == 'Ready' and c['status'] == 'True' for c in n['status'].get('conditions', [])):
            reason = 'node is not Ready and schedulable'
        elif any(t['effect'] in ('NoSchedule', 'NoExecute') and not tolerates(t, configured) for t in n['spec'].get('taints', [])):
            reason = 'an explicit GPU_TOLERATIONS entry is required for this node'
        elif int(n['status'].get('allocatable', {}).get('nvidia.com/gpu', 0)) - used.get(name, 0) < required_gpus:
            reason = 'no unallocated GPU capacity'
        if reason:
            reasons.append(name + ': ' + reason)
            continue
        hostname = labels.get('kubernetes.io/hostname')
        if not hostname:
            reasons.append(name + ': missing hostname label')
            continue
        return {'version': 1, 'nodeSelector': {'kubernetes.io/hostname': hostname},
                'tolerations': configured, 'storageClass': storage['metadata']['name'],
                'storageTopology': topology, 'gpuCount': required_gpus}
    raise ValueError('No compatible available GPU node matches GPU_NODE_SELECTOR/STORAGE_ZONE. ' + '; '.join(reasons))


def validate_plan(plan):
    if not isinstance(plan, dict) or plan.get('version') != 1:
        raise ValueError('Missing or unsupported placement; run 01-setup-project.sh first')
    selector(plan.get('nodeSelector'))
    if not plan['nodeSelector'].get('kubernetes.io/hostname'):
        raise ValueError('Saved placement must select a hostname')
    tolerations(plan.get('tolerations'))
    if not isinstance(plan.get('storageClass'), str) or plan.get('storageTopology') not in ('auto', 'shared'):
        raise ValueError('Invalid saved storage placement')
    return plan


def render_predictor(manifest, plan, project):
    validate_plan(plan)
    if required_gpus(manifest) != plan.get('gpuCount', 1):
        raise ValueError('Predictor GPU count differs from saved placement; rerun setup to validate capacity')
    result = copy.deepcopy(manifest)
    result.setdefault('metadata', {})['namespace'] = project
    result['spec']['predictor']['nodeSelector'] = plan['nodeSelector']
    result['spec']['predictor']['tolerations'] = plan['tolerations']
    return result


def oc_json(*args, input_text=None):
    run = subprocess.run(['oc', *args], input=input_text, capture_output=True, text=True)
    if run.returncode:
        raise ValueError(run.stderr.strip())
    return json.loads(run.stdout) if run.stdout.strip() else None


def main():
    action, project = sys.argv[1:]
    pvc = oc_json('get', 'pvc', 'model-pvc', '-n', project, '--ignore-not-found', '-o', 'json')
    saved = (pvc or {}).get('metadata', {}).get('annotations', {}).get(ANNOTATION)
    if action == 'render':
        if not saved:
            raise ValueError('PVC has no saved placement; run 01-setup-project.sh first')
        plan = validate_plan(json.loads(saved))
        if pvc['spec']['storageClassName'] != plan['storageClass']:
            raise ValueError('PVC and saved placement use different storage classes')
        print(json.dumps(render_predictor(json.load(sys.stdin), plan, project)))
        return
    if action != 'plan':
        raise ValueError('Usage: placement.py [plan|render] PROJECT')
    env = dict(os.environ)
    manifest = oc_json('create', '--dry-run=client', '-n', project, '-f', '-', '-o', 'json',
                       input_text=(Path(__file__).resolve().parent / '03-inference-service.yaml').read_text())
    count = required_gpus(manifest)
    nodes = oc_json('get', 'nodes', '-l', 'nvidia.com/gpu.present=true', '-o', 'json')['items']
    if saved:
        old = validate_plan(json.loads(saved))
        if env.get('GPU_TOLERATIONS') and tolerations(json.loads(env['GPU_TOLERATIONS'])) != old['tolerations']:
            raise ValueError('Saved tolerations differ; use a new project for a different placement')
        env.setdefault('GPU_TOLERATIONS', json.dumps(old['tolerations']))
        env.setdefault('STORAGE_TOPOLOGY', old['storageTopology'])
        nodes = [n for n in nodes if all(n['metadata'].get('labels', {}).get(k) == v for k,v in old['nodeSelector'].items())]
    storage = oc_json('get', 'storageclass', env['STORAGE_CLASS'], '-o', 'json')
    if pvc and pvc.get('spec', {}).get('storageClassName') != storage['metadata']['name']:
        raise ValueError('Existing PVC storage class differs; use its class or a new project')
    if pvc and not saved:
        if pvc.get('status', {}).get('phase') != 'Bound' or not pvc.get('spec', {}).get('volumeName'):
            raise ValueError('Existing PVC has no saved placement and is not Bound; use a new project rather than changing a pending download placement')
        pv = oc_json('get', 'pv', pvc['spec']['volumeName'], '-o', 'json')
        # Legacy PVCs may have a zonal binding. Do not guess a new placement.
        if pv['spec'].get('nodeAffinity'):
            raise ValueError('Existing PVC has topology constraints but no saved placement; use a new project to resolve download and predictor placement together')
    pods = oc_json('get', 'pods', '-A', '-o', 'json')['items']
    plan = make_plan(nodes, pods, storage, env, project, count)
    print(json.dumps(plan, separators=(',', ':')))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, KeyError, TypeError) as error:
        print('Placement error: ' + str(error), file=sys.stderr)
        sys.exit(2)
