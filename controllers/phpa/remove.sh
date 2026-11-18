#!/usr/bin/env bash
set -euo pipefail

if kubectl --context "$KUBE_CONTEXT" get crd predictivehorizontalpodautoscalers.jamiethompson.me >/dev/null 2>&1; then
  kubectl --context "$KUBE_CONTEXT" -n "$SCALE_NAMESPACE" \
    delete predictivehorizontalpodautoscaler scale-app --ignore-not-found
fi