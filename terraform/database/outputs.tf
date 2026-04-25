output "aurora_cluster_endpoint" {
  description = "Aurora cluster endpoint for database connections"
  value       = aws_rds_cluster.aurora.endpoint
}

output "aurora_secret_arn" {
  description = "ARN of the Secrets Manager secret containing DB credentials"
  value       = aws_secretsmanager_secret.aurora.arn
}

output "aurora_database_name" {
  description = "Name of the database"
  value       = aws_rds_cluster.aurora.database_name
}
