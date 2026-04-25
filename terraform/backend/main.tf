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

# 1. ECR Repository — stores your Docker images
resource "aws_ecr_repository" "backend" {
  name                 = "litigation-backend"
  image_tag_mutability = "MUTABLE"
  force_delete         = true
}

# 2. Secrets Manager — stores all API keys the backend needs at runtime
resource "aws_secretsmanager_secret" "backend" {
  name                    = "litigation-backend-secrets"
  recovery_window_in_days = 0
}

resource "aws_secretsmanager_secret_version" "backend" {
  secret_id = aws_secretsmanager_secret.backend.id
  secret_string = jsonencode({
    OPENAI_API_KEY      = var.openai_api_key
    OPENROUTER_API_KEY  = var.openrouter_api_key
    MODEL               = var.openai_model
    CLERK_JWKS_URL      = var.clerk_jwks_url
    CLERK_ISSUER        = var.clerk_issuer
    ALLOWED_ORIGINS     = var.allowed_origins
    PINECONE_API_KEY    = var.pinecone_api_key
    PINECONE_INDEX_HOST = var.pinecone_index_host
    PINECONE_INDEX_NAME = var.pinecone_index_name
    PINECONE_NAMESPACE  = var.pinecone_namespace
    LANGFUSE_PUBLIC_KEY = var.langfuse_public_key
    LANGFUSE_SECRET_KEY = var.langfuse_secret_key
    LANGFUSE_HOST       = var.langfuse_host
  })
}

# 3. IAM Roles — gives App Runner permission to pull from ECR and read secrets
resource "aws_iam_role" "apprunner_ecr" {
  name = "apprunner-ecr-role"

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
  name = "apprunner-instance-role"

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
  name = "apprunner-secrets-policy"
  role = aws_iam_role.apprunner_instance.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["secretsmanager:GetSecretValue"]
      Resource = [aws_secretsmanager_secret.backend.arn]
    }]
  })
}

# 4. OIDC — allows GitHub Actions to authenticate to AWS without stored credentials
resource "aws_iam_openid_connect_provider" "github" {
  url             = "https://token.actions.githubusercontent.com"
  client_id_list  = ["sts.amazonaws.com"]
  thumbprint_list = ["6938fd4d98bab03faadb97b34396831e3780aea1"]
}

resource "aws_iam_role" "github_actions" {
  name = "github-actions-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Federated = aws_iam_openid_connect_provider.github.arn }
      Action    = "sts:AssumeRoleWithWebIdentity"
      Condition = {
        StringLike = {
          "token.actions.githubusercontent.com:sub" = "repo:mrithwik/legal-assitant:*"
        }
        StringEquals = {
          "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com"
        }
      }
    }]
  })
}

resource "aws_iam_role_policy" "github_actions_ecr" {
  name = "github-actions-ecr-policy"
  role = aws_iam_role.github_actions.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "ecr:GetAuthorizationToken",
          "ecr:BatchCheckLayerAvailability",
          "ecr:InitiateLayerUpload",
          "ecr:UploadLayerPart",
          "ecr:CompleteLayerUpload",
          "ecr:PutImage"
        ]
        Resource = "*"
      }
    ]
  })
}

# 5. App Runner Service — runs your Docker container
resource "aws_apprunner_service" "backend" {
  service_name = "litigation-backend"

  source_configuration {
    authentication_configuration {
      access_role_arn = aws_iam_role.apprunner_ecr.arn
    }

    image_repository {
      image_identifier      = "${aws_ecr_repository.backend.repository_url}:latest"
      image_repository_type = "ECR"

      image_configuration {
        port = "8000"

        runtime_environment_secrets = {
          OPENAI_API_KEY      = "${aws_secretsmanager_secret.backend.arn}:OPENAI_API_KEY::"
          OPENROUTER_API_KEY  = "${aws_secretsmanager_secret.backend.arn}:OPENROUTER_API_KEY::"
          MODEL               = "${aws_secretsmanager_secret.backend.arn}:MODEL::"
          CLERK_JWKS_URL      = "${aws_secretsmanager_secret.backend.arn}:CLERK_JWKS_URL::"
          CLERK_ISSUER        = "${aws_secretsmanager_secret.backend.arn}:CLERK_ISSUER::"
          ALLOWED_ORIGINS     = "${aws_secretsmanager_secret.backend.arn}:ALLOWED_ORIGINS::"
          PINECONE_API_KEY    = "${aws_secretsmanager_secret.backend.arn}:PINECONE_API_KEY::"
          PINECONE_INDEX_HOST = "${aws_secretsmanager_secret.backend.arn}:PINECONE_INDEX_HOST::"
          PINECONE_INDEX_NAME = "${aws_secretsmanager_secret.backend.arn}:PINECONE_INDEX_NAME::"
          PINECONE_NAMESPACE  = "${aws_secretsmanager_secret.backend.arn}:PINECONE_NAMESPACE::"
          LANGFUSE_PUBLIC_KEY = "${aws_secretsmanager_secret.backend.arn}:LANGFUSE_PUBLIC_KEY::"
          LANGFUSE_SECRET_KEY = "${aws_secretsmanager_secret.backend.arn}:LANGFUSE_SECRET_KEY::"
          LANGFUSE_HOST       = "${aws_secretsmanager_secret.backend.arn}:LANGFUSE_HOST::"
        }
      }
    }

    auto_deployments_enabled = true
  }

  instance_configuration {
    instance_role_arn = aws_iam_role.apprunner_instance.arn
  }
}
