terraform {
  required_version = ">= 1.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    tls = {
      source  = "hashicorp/tls"
      version = "~> 4.0"
    }
  }

  # Remote state in S3 so CI/CD remembers what infrastructure exists
  backend "s3" {
    bucket  = "mlops-stock-agent-params-tfstate"
    key     = "terraform.tfstate"
    region  = "us-east-1"
    encrypt = true
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
