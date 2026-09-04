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

variable "restrict_ingress_to_cloudflare" {
  description = "When true, the ALB only accepts traffic from Cloudflare's published IP ranges. Set this when the domain is proxied through Cloudflare (e.g. fronted by Cloudflare Access) so the public cannot bypass the edge by hitting the ALB directly. When false, the ALB is open to the internet."
  type        = bool
  default     = false
}

# Cloudflare's published edge ranges (https://api.cloudflare.com/client/v4/ips).
# Only consulted when restrict_ingress_to_cloudflare = true. Refresh if Cloudflare
# publishes new ranges.
variable "cloudflare_ipv4_cidrs" {
  description = "Cloudflare IPv4 ranges allowlisted on the ALB when restrict_ingress_to_cloudflare is true."
  type        = list(string)
  default = [
    "173.245.48.0/20",
    "103.21.244.0/22",
    "103.22.200.0/22",
    "103.31.4.0/22",
    "141.101.64.0/18",
    "108.162.192.0/18",
    "190.93.240.0/20",
    "188.114.96.0/20",
    "197.234.240.0/22",
    "198.41.128.0/17",
    "162.158.0.0/15",
    "104.16.0.0/13",
    "104.24.0.0/14",
    "172.64.0.0/13",
    "131.0.72.0/22",
  ]
}

variable "cloudflare_ipv6_cidrs" {
  description = "Cloudflare IPv6 ranges allowlisted on the ALB when restrict_ingress_to_cloudflare is true."
  type        = list(string)
  default = [
    "2400:cb00::/32",
    "2606:4700::/32",
    "2803:f800::/32",
    "2405:b500::/32",
    "2405:8100::/32",
    "2a06:98c0::/29",
    "2c0f:f248::/32",
  ]
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
