# AWS EKS Deployment Guide

Complete guide to deploy the MLOps Stock Prediction Pipeline to AWS EKS using Terraform.

---

## Table of Contents

1. [Prerequisites](#prerequisites)
2. [Quick Start](#quick-start)
3. [Infrastructure Setup (Terraform)](#infrastructure-setup-terraform)
4. [Build & Push Docker Images (ECR)](#build--push-docker-images-ecr)
5. [Deploy to Kubernetes (EKS)](#deploy-to-kubernetes-eks)
6. [Access Applications](#access-applications)
7. [Monitoring & Logs](#monitoring--logs)
8. [Teardown (Kill Everything)](#teardown-kill-everything)
9. [Troubleshooting](#troubleshooting)
10. [Cost Estimation](#cost-estimation)

---

## Prerequisites

### Install Required Tools

```bash
# AWS CLI
curl "https://awscli.amazonaws.com/AWSCLIV2.pkg" -o "AWSCLIV2.pkg"
sudo installer -pkg AWSCLIV2.pkg -target /

# Terraform
brew install terraform

# kubectl
brew install kubectl

# Docker (ensure Docker Desktop is running)
```

### Configure AWS Credentials

```bash
# Configure with your AWS Access Keys
aws configure

# Verify configuration
aws sts get-caller-identity
```

Output should show your AWS Account ID, User ARN, and User ID.

---

## Quick Start

```bash
# 1. Deploy Infrastructure (~15 min)
cd terraform
terraform init
terraform apply -auto-approve

# 2. Build & Push Images (~5 min)
./scripts/push-to-ecr.sh

# 3. Deploy to EKS (~3 min)
./scripts/deploy-k8s.sh

# 4. Get URLs
kubectl get svc -n mlops
```

---

## Infrastructure Setup (Terraform)

### Step 1: Initialize Terraform

```bash
cd terraform

# Copy example variables
cp terraform.tfvars.example terraform.tfvars

# Edit with your preferences (optional)
# nano terraform.tfvars

# Initialize Terraform
terraform init
```

### Step 2: Review Plan

```bash
terraform plan
```

This will show you:
- 1 VPC with 4 subnets
- 1 EKS cluster with 2 nodes
- 3 ECR repositories
- IAM roles and policies

### Step 3: Apply Infrastructure

```bash
terraform apply
```

Type `yes` when prompted. This takes approximately **15-20 minutes**.

### Step 4: Configure kubectl

```bash
# Get the command from Terraform output
terraform output configure_kubectl

# Run the command (example):
aws eks update-kubeconfig --region us-east-1 --name mlops-stock-cluster

# Verify connection
kubectl get nodes
```

---

## Build & Push Docker Images (ECR)

### Step 1: Login to ECR

```bash
# Get your AWS Account ID
AWS_ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
AWS_REGION="us-east-1"

# Login to ECR
aws ecr get-login-password --region $AWS_REGION | \
  docker login --username AWS --password-stdin \
  $AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com
```

### Step 2: Build Images

```bash
# Navigate to project root
cd /path/to/stock-agent-ops

# Build FastAPI Backend
docker build -t mlops-fastapi:latest -f backend/Dockerfile .

# Build Frontend
docker build -t mlops-frontend:latest -f frontend/Dockerfile ./frontend

# Build Monitoring Dashboard
docker build -t mlops-monitoring:latest -f monitoring_app/Dockerfile ./monitoring_app
```

### Step 3: Tag Images

```bash
# Tag for ECR
docker tag mlops-fastapi:latest \
  $AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/mlops-fastapi:latest

docker tag mlops-frontend:latest \
  $AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/mlops-frontend:latest

docker tag mlops-monitoring:latest \
  $AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/mlops-monitoring:latest
```

### Step 4: Push to ECR

```bash
docker push $AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/mlops-fastapi:latest
docker push $AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/mlops-frontend:latest
docker push $AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/mlops-monitoring:latest
```

### Step 5: Update K8s Manifests

```bash
# Replace placeholder with your Account ID in all manifests
sed -i '' "s/<AWS_ACCOUNT_ID>/$AWS_ACCOUNT_ID/g" k8s/*.yaml

# Verify replacement
grep -r "dkr.ecr" k8s/
```

---

## Deploy to Kubernetes (EKS)

### Step 1: Create Namespace

```bash
kubectl apply -f k8s/namespace.yaml
```

### Step 2: Create Secrets

```bash
# Copy secrets template
cp k8s/secrets.yaml.example k8s/secrets.yaml

# Edit with your base64-encoded values
# To encode: echo -n "your-value" | base64
nano k8s/secrets.yaml

# Apply secrets
kubectl apply -f k8s/secrets.yaml
```

### Step 3: Deploy All Resources

```bash
# Apply all manifests
kubectl apply -f k8s/volumes.yaml
kubectl apply -f k8s/redis.yaml
kubectl apply -f k8s/qdrant.yaml
kubectl apply -f k8s/prometheus.yaml
kubectl apply -f k8s/grafana.yaml
kubectl apply -f k8s/fastapi.yaml
kubectl apply -f k8s/frontend.yaml
kubectl apply -f k8s/monitoring-app.yaml

# Or apply all at once
kubectl apply -f k8s/
```

### Step 4: Verify Deployment

```bash
# Check pods
kubectl get pods -n mlops

# Wait for all pods to be Running
kubectl wait --for=condition=Ready pods --all -n mlops --timeout=300s

# Check services
kubectl get svc -n mlops
```

---

## Access Applications

### Get LoadBalancer URLs

```bash
# Get all external URLs
kubectl get svc -n mlops -o wide

# Get specific URLs
echo "FastAPI: $(kubectl get svc fastapi -n mlops -o jsonpath='{.status.loadBalancer.ingress[0].hostname}'):8000"
echo "Frontend: $(kubectl get svc frontend -n mlops -o jsonpath='{.status.loadBalancer.ingress[0].hostname}'):8501"
echo "Grafana: $(kubectl get svc grafana -n mlops -o jsonpath='{.status.loadBalancer.ingress[0].hostname}'):3000"
echo "Monitoring: $(kubectl get svc monitoring-app -n mlops -o jsonpath='{.status.loadBalancer.ingress[0].hostname}'):8502"
```

### Test Endpoints

```bash
# Get FastAPI URL
FASTAPI_URL=$(kubectl get svc fastapi -n mlops -o jsonpath='{.status.loadBalancer.ingress[0].hostname}')

# Health check
curl http://$FASTAPI_URL:8000/health

# Analyze stock
curl -X POST http://$FASTAPI_URL:8000/analyze \
  -H "Content-Type: application/json" \
  -d '{"ticker": "AAPL"}'
```

---

## Monitoring & Logs

### View Pod Logs

```bash
# FastAPI logs
kubectl logs -f deployment/fastapi -n mlops

# All pods
kubectl logs -f -l app=fastapi -n mlops
```

### Access Grafana

1. Get Grafana URL from services
2. Login: `admin` / `admin`
3. Add Prometheus data source: `http://prometheus:9090`

### Check Resource Usage

```bash
kubectl top pods -n mlops
kubectl top nodes
```

---

## Teardown (Kill Everything)

> ⚠️ **WARNING**: This will destroy ALL resources and data. This action is irreversible.

### Step 1: Delete Kubernetes Resources

```bash
# Delete all resources in the namespace
kubectl delete -f k8s/

# Delete namespace
kubectl delete namespace mlops

# Verify deletion
kubectl get all -n mlops
```

### Step 2: Delete ECR Images (REQUIRED)

Terraform cannot destroy non-empty ECR repositories. You MUST run this before `terraform destroy`.

```bash
# Set your variables
AWS_ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
AWS_REGION="us-east-1"

# Delete fastapi images
aws ecr batch-delete-image \
  --repository-name mlops-fastapi \
  --image-ids "$(aws ecr list-images --repository-name mlops-fastapi --query 'imageIds[*]' --output json)" \
  --region $AWS_REGION || true

# Delete frontend images
aws ecr batch-delete-image \
  --repository-name mlops-frontend \
  --image-ids "$(aws ecr list-images --repository-name mlops-frontend --query 'imageIds[*]' --output json)" \
  --region $AWS_REGION || true

# Delete monitoring images
aws ecr batch-delete-image \
  --repository-name mlops-monitoring \
  --image-ids "$(aws ecr list-images --repository-name mlops-monitoring --query 'imageIds[*]' --output json)" \
  --region $AWS_REGION || true
```

### Step 3: Destroy Terraform Infrastructure

```bash
cd terraform

# Destroy all AWS resources
terraform destroy

# Type 'yes' when prompted
```

This will delete:
- EKS Cluster and Node Groups
- ECR Repositories
- VPC, Subnets, NAT Gateway
- IAM Roles and Policies
- All associated resources

### Step 4: Verify Complete Cleanup

```bash
# Check no EKS clusters remain
aws eks list-clusters --region us-east-1

# Check no ECR repositories remain
aws ecr describe-repositories --region us-east-1

# Check CloudWatch log groups (manual cleanup if needed)
aws logs describe-log-groups --log-group-name-prefix /aws/eks/mlops
```

### One-Liner Kill Everything

```bash
# ⚠️ DANGER: Destroys everything without confirmation
kubectl delete -f k8s/ --ignore-not-found && \
kubectl delete namespace mlops --ignore-not-found && \
cd terraform && terraform destroy -auto-approve
```

---

## Troubleshooting

### Pods Not Starting

```bash
# Check pod status
kubectl describe pod <pod-name> -n mlops

# Check events
kubectl get events -n mlops --sort-by='.lastTimestamp'
```

### Image Pull Errors

```bash
# Verify ECR login
aws ecr get-login-password --region us-east-1 | docker login --username AWS --password-stdin <account-id>.dkr.ecr.us-east-1.amazonaws.com

# Check image exists
aws ecr describe-images --repository-name mlops-fastapi --region us-east-1
```

### Node Issues

```bash
# Check node status
kubectl describe nodes

# Check node group in AWS Console
aws eks describe-nodegroup --cluster-name mlops-stock-cluster --nodegroup-name mlops-stock-cluster-nodes
```

---

## Cost Estimation

| Resource | Monthly Cost (Approx) |
|----------|----------------------|
| EKS Control Plane | $73 |
| 2x t3.medium Nodes | $60 |
| NAT Gateway | $32 + data |
| EBS Storage (9GB) | $1 |
| Load Balancers (4x) | $65 |
| **Total** | **~$230/month** |

### Cost Saving Tips

1. Use Spot instances for nodes (add to Terraform)
2. Reduce to 1 node during development
3. Delete NAT Gateway when not needed
4. Use internal services instead of LoadBalancers

---

## Files Reference

```
terraform/
├── main.tf              # Provider config
├── variables.tf         # Input variables
├── vpc.tf               # VPC, subnets, NAT
├── eks.tf               # EKS cluster, nodes
├── ecr.tf               # ECR repositories
├── iam.tf               # IAM roles
├── outputs.tf           # Output values
└── terraform.tfvars     # Your values (gitignored)

k8s/
├── namespace.yaml       # mlops namespace
├── secrets.yaml         # Credentials (gitignored)
├── volumes.yaml         # PVCs
├── redis.yaml           # Redis
├── qdrant.yaml          # Qdrant
├── prometheus.yaml      # Prometheus
├── grafana.yaml         # Grafana
├── fastapi.yaml         # Backend
├── frontend.yaml        # Frontend
└── monitoring-app.yaml  # Monitoring
```


## Check what's running

# Check EKS clusters
aws eks list-clusters --region us-east-1

# Check EC2 instances  
aws ec2 describe-instances --region us-east-1 --query 'Reservations[*].Instances[*].[InstanceId,State.Name,InstanceType]' --output table

# Check LoadBalancers (these cost money!)
aws elbv2 describe-load-balancers --region us-east-1 --query 'LoadBalancers[*].[LoadBalancerName,State.Code]' --output table

# Check NAT Gateways (these cost ~$32/month each!)
aws ec2 describe-nat-gateways --region us-east-1 --query 'NatGateways[*].[NatGatewayId,State]' --output table

# Check ECR repositories
aws ecr describe-repositories --region us-east-1 --query 'repositories[*].repositoryName' --output table