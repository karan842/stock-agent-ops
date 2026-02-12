#!/bin/bash
set -e

AWS_REGION="us-east-1"
BUCKET_NAME="mlops-stock-agent-params-tfstate"

echo "Setting up Terraform S3 backend..."

if aws s3api head-bucket --bucket "$BUCKET_NAME" 2>/dev/null; then
    echo "Bucket $BUCKET_NAME already exists."
else
    echo "Creating bucket $BUCKET_NAME..."
    aws s3api create-bucket --bucket "$BUCKET_NAME" --region "$AWS_REGION"
    aws s3api put-bucket-versioning --bucket "$BUCKET_NAME" --versioning-configuration Status=Enabled
    echo "Bucket created."
fi

echo "Backend ready."
