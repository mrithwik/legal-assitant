output "ecr_repository_url" {
  description = "ECR repository URL for pushing Docker images"
  value       = aws_ecr_repository.backend.repository_url
}

output "app_runner_url" {
  description = "App Runner service URL"
  value       = "https://${aws_apprunner_service.backend.service_url}"
}

output "github_actions_role_arn" {
  description = "IAM role ARN for GitHub Actions OIDC authentication"
  value       = aws_iam_role.github_actions.arn
}
