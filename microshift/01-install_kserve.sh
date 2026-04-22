#!/bin/bash
# Install KServe on MicroShift (via minc)
# MicroShift uses OpenShift SCCs, so extra permissions are needed for Istio.

set -eo pipefail

KSERVE_VERSION=v0.15.2
CERT_MANAGER_VERSION=v1.16.1
KNATIVE_OPERATOR_VERSION=v1.15.7
KNATIVE_SERVING_VERSION=1.15.2
ISTIO_VERSION=1.27.1
GATEWAY_API_VERSION=v1.2.1

echo "📦 Installing Gateway API CRDs ..."
oc apply -f https://github.com/kubernetes-sigs/gateway-api/releases/download/${GATEWAY_API_VERSION}/standard-install.yaml

echo ""
echo "📦 Installing Istio ..."
helm repo add istio https://istio-release.storage.googleapis.com/charts --force-update
helm install istio-base istio/base -n istio-system --create-namespace --wait
helm install istiod istio/istiod -n istio-system --wait --version ${ISTIO_VERSION}

# Install ingressgateway first, then grant SCC and restart
echo ""
echo "📦 Installing Istio Ingress Gateway ..."
helm install istio-ingressgateway istio/gateway -n istio-system --version ${ISTIO_VERSION} --wait=false

echo ""
echo "🔓 Granting SCC permissions for Istio ingressgateway ..."
oc adm policy add-scc-to-user anyuid -z istio-ingressgateway -n istio-system

echo "⏳ Restarting ingressgateway to pick up SCC ..."
oc rollout restart deploy/istio-ingressgateway -n istio-system
oc rollout status deploy/istio-ingressgateway -n istio-system --timeout=120s

echo ""
echo "📦 Installing cert-manager ..."
oc apply -f https://github.com/cert-manager/cert-manager/releases/download/${CERT_MANAGER_VERSION}/cert-manager.yaml
echo "⏳ Waiting for cert-manager to be ready ..."
oc wait --for=condition=Available deployment/cert-manager-webhook -n cert-manager --timeout=120s

echo ""
echo "📦 Installing Knative Operator ..."
oc apply -f https://github.com/knative/operator/releases/download/knative-${KNATIVE_OPERATOR_VERSION}/operator.yaml
echo "⏳ Waiting for Knative Operator ..."
oc wait --for=condition=Available deployment/knative-operator -n knative-operator --timeout=120s

echo ""
echo "📦 Creating KnativeServing with Istio ingress ..."
cat <<EOF | oc apply -f -
apiVersion: v1
kind: Namespace
metadata:
  name: knative-serving
---
apiVersion: operator.knative.dev/v1beta1
kind: KnativeServing
metadata:
  name: knative-serving
  namespace: knative-serving
spec:
  version: "${KNATIVE_SERVING_VERSION}"
  ingress:
    istio:
      enabled: true
  config:
    network:
      ingress-class: "istio.ingress.networking.knative.dev"
EOF

echo ""
echo "🔗 Configuring Knative Istio gateway routing ..."
cat <<CMEOF | oc apply -f -
apiVersion: v1
kind: ConfigMap
metadata:
  name: config-istio
  namespace: knative-serving
data:
  external-gateways: |
    - name: knative-ingress-gateway
      namespace: knative-serving
      service: istio-ingressgateway.istio-system.svc.cluster.local
  local-gateways: |
    - name: knative-local-gateway
      namespace: knative-serving
      service: knative-local-gateway.istio-system.svc.cluster.local
CMEOF

echo ""
echo "🔗 Creating Knative Istio Gateways and local gateway service ..."
cat <<GWEOF | oc apply -f -
apiVersion: networking.istio.io/v1beta1
kind: Gateway
metadata:
  name: knative-ingress-gateway
  namespace: knative-serving
spec:
  selector:
    istio: ingressgateway
  servers:
  - port:
      number: 80
      name: http
      protocol: HTTP
    hosts:
    - "*"
---
apiVersion: networking.istio.io/v1beta1
kind: Gateway
metadata:
  name: knative-local-gateway
  namespace: knative-serving
spec:
  selector:
    istio: ingressgateway
  servers:
  - port:
      number: 80
      name: http
      protocol: HTTP
    hosts:
    - "*"
---
apiVersion: v1
kind: Service
metadata:
  name: knative-local-gateway
  namespace: istio-system
  labels:
    app: istio-ingressgateway
spec:
  type: ClusterIP
  selector:
    app: istio-ingressgateway
  ports:
  - name: http2
    port: 80
    targetPort: 80
GWEOF

echo "⏳ Waiting for Knative Serving pods ..."
echo "   (waiting for activator deployment to appear...)"
until oc get deployment/activator -n knative-serving &>/dev/null; do sleep 5; done
oc wait --for=condition=Available deployment/activator -n knative-serving --timeout=300s
oc wait --for=condition=Available deployment/controller -n knative-serving --timeout=300s

echo ""
echo "⏳ Ensuring cert-manager webhook is ready ..."
oc wait --for=condition=Available deployment/cert-manager -n cert-manager --timeout=120s
oc wait --for=condition=Available deployment/cert-manager-cainjector -n cert-manager --timeout=120s
oc wait --for=condition=Available deployment/cert-manager-webhook -n cert-manager --timeout=120s
echo "   (waiting for cert-manager webhook to accept requests...)"
until oc apply -f - <<TESTEOF 2>/dev/null
apiVersion: cert-manager.io/v1
kind: Issuer
metadata:
  name: test-issuer
  namespace: default
spec:
  selfSigned: {}
TESTEOF
do sleep 5; done
oc delete issuer test-issuer -n default 2>/dev/null || true

echo ""
echo "📦 Installing KServe ..."
oc apply --server-side -f https://github.com/kserve/kserve/releases/download/${KSERVE_VERSION}/kserve.yaml
echo "⏳ Waiting for KServe webhook cert to be issued ..."
until oc get secret kserve-webhook-server-cert -n kserve &>/dev/null; do sleep 5; done
echo "⏳ Waiting for KServe controller ..."
oc wait --for=condition=Available deployment/kserve-controller-manager -n kserve --timeout=300s

echo ""
echo "📦 Installing KServe cluster resources (runtimes) ..."
oc apply --server-side -f https://github.com/kserve/kserve/releases/download/${KSERVE_VERSION}/kserve-cluster-resources.yaml

echo ""
echo "✅ KServe installation complete on MicroShift!"
echo ""
echo "Next steps:"
echo "  1. Create HuggingFace secret:  oc create secret generic huggingface-secret --from-literal=token=<your_token>"
echo "  2. Deploy model:               oc apply -f 02-inference-service.yaml"
echo "  3. Run inference:              ./03-run-inference.sh"
