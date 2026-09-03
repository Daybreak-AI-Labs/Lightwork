output "alb_dns_name" {
  description = "Public DNS name of the load balancer (the app's URL until a custom domain is attached)."
  value       = aws_lb.main.dns_name
}

output "app_url" {
  description = "HTTP URL to reach the dashboard."
  value       = "http://${aws_lb.main.dns_name}"
}

output "ecr_repository_url" {
  description = "ECR repository the image is pushed to."
  value       = aws_ecr_repository.app.repository_url
}

output "codebuild_project" {
  description = "CodeBuild project that builds and pushes the image."
  value       = aws_codebuild_project.image.name
}

output "build_source_bucket" {
  description = "S3 bucket the build source zip is uploaded to (key: source.zip)."
  value       = aws_s3_bucket.build.bucket
}

output "rds_endpoint" {
  description = "RDS Postgres endpoint (private)."
  value       = aws_db_instance.main.address
}

output "ecs_cluster" {
  value = aws_ecs_cluster.main.name
}

output "ecs_service" {
  value = aws_ecs_service.app.name
}

output "dashboard_token_secret" {
  description = "Secrets Manager id holding the dashboard bearer token (fetch to log in)."
  value       = aws_secretsmanager_secret.dashboard_token.name
}

output "anthropic_secret_id" {
  description = "Secrets Manager id to put the real provider API key into."
  value       = aws_secretsmanager_secret.anthropic_api_key.name
}
