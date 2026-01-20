terraform {
  required_version = ">= 1.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }

  # REMOTE BACKEND CONFIGURATION
  # This is required for GitHub Actions to prevent state corruption.
  # We will use S3 for state storage and DynamoDB for state locking.
  # The bucket name will be dynamic based on user variables if we were using a template,
  # but for now we hardcode a unique name pattern.
  # Run `scripts/setup_tf_backend.sh` to create these resources first.
  backend "s3" {
    bucket         = "mlops-stock-agent-params-tfstate"  # Unique bucket name
    key            = "terraform.tfstate"
    region         = "us-east-1"
    encrypt        = true
    dynamodb_table = "mlops-stock-agent-params-tf-lock"  # DynamoDB table for locking
  }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project     = "MLOps-Stock-Pipeline"
      Environment = var.environment
      ManagedBy   = "Terraform"
    }
  }
}

# Data source for available AZs
data "aws_availability_zones" "available" {
  state = "available"
}

data "aws_caller_identity" "current" {}
