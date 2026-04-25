terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}

provider "aws" {
  region = var.aws_region
}

# 1. ECR Repository — stores your frontend Docker images
resource "aws_ecr_repository" "frontend" {
  name                 = "litigation-frontend"
  image_tag_mutability = "MUTABLE"
  force_delete         = true
}

# 2. Secrets Manager — stores server-side secrets injected at runtime
resource "aws_secretsmanager_secret" "frontend" {
  name                    = "litigation-frontend-secrets"
  recovery_window_in_days = 0
}

resource "aws_secretsmanager_secret_version" "frontend" {
  secret_id = aws_secretsmanager_secret.frontend.id
  secret_string = jsonencode({
    CLERK_SECRET_KEY = var.clerk_secret_key
  })
}

# 3. IAM Roles — gives App Runner permission to pull from ECR and read secrets
resource "aws_iam_role" "apprunner_ecr" {
  name = "apprunner-frontend-ecr-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "build.apprunner.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "apprunner_ecr" {
  role       = aws_iam_role.apprunner_ecr.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSAppRunnerServicePolicyForECRAccess"
}

resource "aws_iam_role" "apprunner_instance" {
  name = "apprunner-frontend-instance-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "tasks.apprunner.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "apprunner_secrets" {
  name = "apprunner-frontend-secrets-policy"
  role = aws_iam_role.apprunner_instance.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["secretsmanager:GetSecretValue"]
      Resource = [aws_secretsmanager_secret.frontend.arn]
    }]
  })
}

# 4. App Runner Service — runs your frontend container
resource "aws_apprunner_service" "frontend" {
  service_name = "litigation-frontend"

  source_configuration {
    authentication_configuration {
      access_role_arn = aws_iam_role.apprunner_ecr.arn
    }

    image_repository {
      image_identifier      = "${aws_ecr_repository.frontend.repository_url}:latest"
      image_repository_type = "ECR"

      image_configuration {
        port = "3000"

        runtime_environment_secrets = {
          CLERK_SECRET_KEY = "${aws_secretsmanager_secret.frontend.arn}:CLERK_SECRET_KEY::"
        }
      }
    }

    auto_deployments_enabled = true
  }

  instance_configuration {
    instance_role_arn = aws_iam_role.apprunner_instance.arn
  }
}
