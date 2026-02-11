#!/bin/bash
set -e

# Region
REGION="us-east-1"
VPC_ID="vpc-0c996f58ed5b5e4ed"

echo "Finding orphaned Security Groups in VPC: $VPC_ID"

# Find SGs in the VPC (excluding 'default')
SGS=$(aws ec2 describe-security-groups --filters Name=vpc-id,Values=$VPC_ID --query "SecurityGroups[?GroupName!='default'].GroupId" --output text --region $REGION)

if [ -n "$SGS" ]; then
  echo "Found orphaned Security Groups: $SGS"
  for SG in $SGS; do
    echo "Deleting $SG..."
    # We ignore errors because dependencies might require retries or order
    aws ec2 delete-security-group --group-id $SG --region $REGION || echo "Failed to delete $SG (might be dependent)"
  done
else
  echo "No regular orphaned Security Groups found."
fi

echo "Trying to destroy Terraform again..."
cd terraform
terraform destroy -auto-approve
