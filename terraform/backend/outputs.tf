output "ecr_repository_url" {
  description = "ECR repository URL for pushing Docker images"
  value       = aws_ecr_repository.backend.repository_url
}

output "app_runner_url" {
  description = "App Runner service URL"
  value       = "https://${aws_apprunner_service.backend.service_url}"
}
