#!/bin/bash
set -e

# Configuration
AWS_REGION="us-east-1"
CLUSTER_NAME="mlops-stock-cluster"

echo "================================================================="
echo "WARNING: This script will DESTROY ALL AWS resources for this project."
echo "This includes EKS, VPC, ECR images, Load Balancers, and Logs."
echo "Cost will go to $0 after this completes."
echo "================================================================="
read -p "Are you sure you want to proceed? (y/N) " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "Aborting."
    exit 1
fi

# 1. Empty ECR Repositories (Terraform dependency)
echo "Step 1: Emptying ECR Repositories..."
REPOS=("mlops-fastapi" "mlops-frontend" "mlops-monitoring")
for REPO in "${REPOS[@]}"; do
    echo "  Cleaning $REPO..."
    # List images and delete them if they exist
    IMAGES=$(aws ecr list-images --repository-name $REPO --region $AWS_REGION --query 'imageIds[*]' --output json 2>/dev/null || echo "[]")
    if [ "$IMAGES" != "[]" ]; then
        aws ecr batch-delete-image --repository-name $REPO --region $AWS_REGION --image-ids "$IMAGES" >/dev/null
        echo "    Images deleted."
    else
        echo "    Already empty or not found."
    fi
done

# 2. Cleanup Kubernetes Resources (Load Balancers)
# Attempt to delete services to trigger LB deletion if cluster is still up
echo "Step 2: Cleaning up Load Balancers..."
if aws eks describe-cluster --name $CLUSTER_NAME --region $AWS_REGION >/dev/null 2>&1; then
    echo "  Cluster is active, attempting detailed cleanup via kubectl..."
    aws eks update-kubeconfig --name $CLUSTER_NAME --region $AWS_REGION
    # Delete services with LoadBalancers explicitly
    kubectl delete svc --all -n mlops --ignore-not-found
    # Delete the namespace
    kubectl delete namespace mlops --ignore-not-found
else
    echo "  Cluster not found or not accessible. Skipping kubectl steps."
fi

# 3. Destroy Terraform Infrastructure
echo "Step 3: Running Terraform Destroy..."
cd terraform
# Initialize if needed (in case of fresh clone)
terraform init -upgrade
terraform destroy -auto-approve
cd ..

# 4. Cleanup Log Groups
echo "Step 4: Cleaning up CloudWatch Log Groups..."
aws logs delete-log-group --log-group-name "/aws/eks/$CLUSTER_NAME/cluster" --region $AWS_REGION 2>/dev/null || true
echo "  Log groups deleted."

# 5. Final Verification (The "100% Clean" Check)
echo "================================================================="
echo "Final Verification Report (Should all be empty/terminated):"
echo "================================================================="

echo "1. Active EC2 Instances:"
aws ec2 describe-instances --region $AWS_REGION \
    --filters "Name=instance-state-name,Values=running,pending,stopping,stopped" \
    --query "Reservations[*].Instances[*].[InstanceId,State.Name,Tags[?Key=='Name'].Value|[0]]" --output table

echo "2. Elastic IPs (Allocation IDs):"
aws ec2 describe-addresses --region $AWS_REGION --query "Addresses[*].[AllocationId,PublicIp]" --output table

echo "3. Load Balancers (Classic & v2):"
aws elbv2 describe-load-balancers --region $AWS_REGION --query "LoadBalancers[*].LoadBalancerName" --output table
aws elb describe-load-balancers --region $AWS_REGION --query "LoadBalancerDescriptions[*].LoadBalancerName" --output table

echo "4. NAT Gateways (Available):"
aws ec2 describe-nat-gateways --region $AWS_REGION \
    --filter "Name=state,Values=available" \
    --query "NatGateways[*].NatGatewayId" --output table

echo "5. EKS Clusters:"
aws eks list-clusters --region $AWS_REGION --query "clusters" --output table

echo "6. ECR Repositories:"
aws ecr describe-repositories --region $AWS_REGION --query "repositories[*].repositoryName" --output table

echo "================================================================="
echo "If any resources remain listed above, please delete them manually via AWS Console."
echo "Cleanup Complete."
