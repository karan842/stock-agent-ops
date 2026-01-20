#!/bin/bash
set -e

# Configuration
AWS_REGION="us-east-1"
BUCKET_NAME="mlops-stock-agent-params-tfstate"
TABLE_NAME="mlops-stock-agent-params-tf-lock"

echo "Setting up Terraform Remote Backend resources in AWS..."

# 1. Create S3 Bucket
if aws s3api head-bucket --bucket "$BUCKET_NAME" 2>/dev/null; then
    echo "Bucket $BUCKET_NAME already exists."
else
    echo "Creating bucket $BUCKET_NAME..."
    aws s3api create-bucket --bucket "$BUCKET_NAME" --region "$AWS_REGION"
    # Enable versioning
    aws s3api put-bucket-versioning --bucket "$BUCKET_NAME" --versioning-configuration Status=Enabled
    echo "Bucket created and versioning enabled."
fi

# 2. Create DynamoDB Table
if aws dynamodb describe-table --table-name "$TABLE_NAME" --region "$AWS_REGION" >/dev/null 2>&1; then
    echo "DynamoDB table $TABLE_NAME already exists."
else
    echo "Creating DynamoDB table $TABLE_NAME..."
    aws dynamodb create-table \
        --table-name "$TABLE_NAME" \
        --attribute-definitions AttributeName=LockID,AttributeType=S \
        --key-schema AttributeName=LockID,KeyType=HASH \
        --billing-mode PAY_PER_REQUEST \
        --region "$AWS_REGION"
    echo "DynamoDB table created."
fi

echo "Backend Setup Complete!"
echo "Now you can run 'terraform init' in the terraform/ directory."
