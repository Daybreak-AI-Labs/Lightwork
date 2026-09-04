variable "region" {
  description = "AWS region to deploy into."
  type        = string
  default     = "us-east-2"
}

variable "aws_profile" {
  description = "Local AWS CLI profile used by Terraform (authenticated via `aws login`)."
  type        = string
  default     = "lightwork"
}

variable "project" {
  description = "Short name used to prefix resources."
  type        = string
  default     = "lightwork"
}

variable "environment" {
  description = "Deployment environment name."
  type        = string
  default     = "prod"
}

variable "vpc_cidr" {
  description = "CIDR for the VPC."
  type        = string
  default     = "10.20.0.0/16"
}

variable "container_port" {
  description = "Port the maverick dashboard listens on inside the container."
  type        = number
  default     = 8765
}

variable "domain_name" {
  description = "Custom domain served over HTTPS (ACM cert + ALB 443 listener). Empty disables the cert/listener and leaves the stack HTTP-only on the ALB DNS name. DNS for this name is managed in Cloudflare, not Route 53."
  type        = string
  default     = "lightwork.daybreakailabs.com"
}

variable "health_check_path" {
  description = "ALB target-group health check path (deep liveness endpoint)."
  type        = string
  default     = "/livez"
}

variable "desired_count" {
  description = "Number of Fargate tasks. The control plane is single-replica by design."
  type        = number
  default     = 1
}

variable "task_cpu" {
  description = "Fargate task CPU units (1024 = 1 vCPU)."
  type        = string
  default     = "1024"
}

variable "task_memory" {
  description = "Fargate task memory (MiB)."
  type        = string
  default     = "2048"
}

variable "image_tag" {
  description = "ECR image tag the service runs."
  type        = string
  default     = "latest"
}

variable "db_instance_class" {
  description = "RDS instance class."
  type        = string
  default     = "db.t4g.micro"
}

variable "db_allocated_storage" {
  description = "RDS allocated storage (GB)."
  type        = number
  default     = 20
}

variable "db_name" {
  description = "Initial Postgres database name."
  type        = string
  default     = "maverick"
}

variable "db_username" {
  description = "Postgres master username."
  type        = string
  default     = "maverick"
}
