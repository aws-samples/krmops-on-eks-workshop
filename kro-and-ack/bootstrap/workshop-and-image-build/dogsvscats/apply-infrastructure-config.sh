#!/bin/bash

# Script to apply the infrastructure ConfigMap to the cluster
# This can be used independently if needed

set -e

echo "Applying infrastructure ConfigMap to the cluster..."

# Check if kubectl is available
if ! command -v kubectl &> /dev/null; then
    echo "Error: kubectl is not available. Please ensure you have access to the Kubernetes cluster."
    exit 1
fi

# Apply the ConfigMap
kubectl apply -f infrastructure-config.yaml

echo "Infrastructure ConfigMap applied successfully!"

# Verify the ConfigMap was created
echo "Verifying ConfigMap contents:"
kubectl get configmap krmops-infrastructure-config -o yaml