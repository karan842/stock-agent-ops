#!/bin/bash
set -e

# Load .env
# Handle potential quoting in .env or special chars
# We mistakenly source .env might be risky if it has comments inline not handled well, but standard .env usually fine.
# Better to manually extract if possible or just source.
set -a
source .env
set +a

# Start creating the file
# Ensure directory exists
mkdir -p k8s

cat <<EOF > k8s/secrets.yaml
apiVersion: v1
kind: Secret
metadata:
  name: mlops-secrets
  namespace: mlops
type: Opaque
data:
  AWS_ACCESS_KEY_ID: $(echo -n "$AWS_ACCESS_KEY_ID" | base64)
  AWS_SECRET_ACCESS_KEY: $(echo -n "$AWS_SECRET_ACCESS_KEY" | base64)
  AWS_REGION: $(echo -n "$AWS_REGION" | base64)
  DAGSHUB_USERNAME: $(echo -n "$DAGSHUB_USER_NAME" | base64)
  DAGSHUB_TOKEN: $(echo -n "$DAGSHUB_TOKEN" | base64)
  MLFLOW_TRACKING_USERNAME: $(echo -n "$DAGSHUB_USER_NAME" | base64)
  MLFLOW_TRACKING_PASSWORD: $(echo -n "$DAGSHUB_TOKEN" | base64)
  FINNHUB_API_KEY: $(echo -n "$FMI_API_KEY" | base64)
EOF

echo "Generated k8s/secrets.yaml"
