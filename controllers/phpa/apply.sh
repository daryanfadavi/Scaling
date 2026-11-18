#!/usr/bin/env bash
set -euo pipefail

VERSION="v0.13.2"
CHART="https://github.com/jthomperoo/predictive-horizontal-pod-autoscaler/releases/download/${VERSION}/predictive-horizontal-pod-autoscaler-${VERSION}.tgz"

helm --kube-context "$KUBE_CONTEXT" upgrade --install phpa-operator "$CHART" \
  --namespace default --wait --timeout 5m

kubectl --context "$KUBE_CONTEXT" -n default rollout status \
  deployment/predictive-horizontal-pod-autoscaler --timeout=180s