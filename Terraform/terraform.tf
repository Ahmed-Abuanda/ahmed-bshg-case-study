terraform {
  required_providers {
    opensearch = {
      source  = "opensearch-project/opensearch"
      version = "~> 2.0"
    }
    aws = {
      source  = "hashicorp/aws"
      version = "5.81.0"
    }
  }
}

provider "aws" {
  region = var.aws_region
}

provider "opensearch" {
  url         = "https://${aws_opensearch_domain.rag_db.endpoint}"
  aws_region  = var.aws_region
  healthcheck = false
}
