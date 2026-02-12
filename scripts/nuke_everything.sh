#!/bin/bash
set -e

# Configuration
BUCKET_NAME="mlops-stock-agent-params-tfstate"
TABLE_NAME="mlops-stock-agent-params-tf-lock"
AWS_REGION="us-east-1"

echo "================================================================="
echo "WARNING: TOTAL DESTRUCTION MODE"
echo "This script will:"
echo "1. Run nuke_aws.sh to destroy EKS, VPC, ECR, etc."
echo "2. PERMANENTLY DELETE the Terraform State S3 Bucket: $BUCKET_NAME"
echo "3. PERMANENTLY DELETE the Terraform Lock Table: $TABLE_NAME"
echo "================================================================="
read -p "Are you absolutely sure? (y/N) " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "Aborting."
    exit 1
fi

# 1. Run standard nuke
echo "Running standard infrastructure nuke..."
# pipe yes to avoid second prompt if nuke_aws.sh prompts
yes | ./scripts/nuke_aws.sh || true

echo "Standard nuke complete. Now destroying backend..."

# 2. Delete S3 Bucket
if aws s3api head-bucket --bucket "$BUCKET_NAME" 2>/dev/null; then
    echo "Deleting S3 bucket $BUCKET_NAME..."
    # --force deletes all objects inside first
    aws s3 rb "s3://$BUCKET_NAME" --force --region "$AWS_REGION"
    echo "Bucket deleted."
else
    echo "Bucket $BUCKET_NAME not found (already deleted?)"
fi

# 3. Delete DynamoDB Table
if aws dynamodb describe-table --table-name "$TABLE_NAME" --region "$AWS_REGION" >/dev/null 2>&1; then
    echo "Deleting DynamoDB table $TABLE_NAME..."
    aws dynamodb delete-table --table-name "$TABLE_NAME" --region "$AWS_REGION"
    echo "Table deleted."
else
    echo "Table $TABLE_NAME not found (already deleted?)"
fi

echo "================================================================="
echo "TOTAL CLEANUP COMPLETE. All AWS resources should be gone."
echo "================================================================="
