terraform {
  # 1.10 is the floor for the S3 backend's native lockfile locking used below.
  required_version = ">= 1.10"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.60"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }

  # Remote state in S3 with native (lockfile) locking -- no DynamoDB table
  # needed on Terraform >= 1.10. The bucket is created out-of-band (it can't
  # bootstrap itself) and is versioned + SSE-encrypted + public-access-blocked.
  backend "s3" {
    bucket       = "lightwork-tfstate-589997396906"
    key          = "lightwork/prod/terraform.tfstate"
    region       = "us-east-2"
    encrypt      = true
    use_lockfile = true
  }
}
