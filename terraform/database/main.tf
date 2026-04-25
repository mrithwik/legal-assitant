terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.0"
    }
  }
}

provider "aws" {
  region = var.aws_region
}

# 1. Security Group — controls what traffic can reach the database
resource "aws_security_group" "aurora" {
  name        = "aurora-sg"
  description = "Allow PostgreSQL access"

  ingress {
    from_port   = 5432
    to_port     = 5432
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

# 2. Subnet Group — tells Aurora which subnets to use
data "aws_subnets" "default" {
  filter {
    name   = "defaultForAz"
    values = ["true"]
  }
}

resource "aws_db_subnet_group" "aurora" {
  name       = "aurora-subnet-group"
  subnet_ids = data.aws_subnets.default.ids
}

# 3. Random Password — auto-generated secure password for the database
resource "random_password" "aurora" {
  length  = 32
  special = false
}

# 4. Aurora Serverless v2 Cluster — the actual database
resource "aws_rds_cluster" "aurora" {
  cluster_identifier      = "litigation-db"
  engine                  = "aurora-postgresql"
  engine_mode             = "provisioned"
  engine_version          = "15.12"
  database_name           = "litigation"
  master_username         = "postgres"
  master_password         = random_password.aurora.result
  db_subnet_group_name    = aws_db_subnet_group.aurora.name
  vpc_security_group_ids  = [aws_security_group.aurora.id]
  skip_final_snapshot     = true

  serverlessv2_scaling_configuration {
    min_capacity = var.min_capacity
    max_capacity = var.max_capacity
  }
}

resource "aws_rds_cluster_instance" "aurora" {
  cluster_identifier = aws_rds_cluster.aurora.id
  instance_class     = "db.serverless"
  engine             = aws_rds_cluster.aurora.engine
  engine_version     = aws_rds_cluster.aurora.engine_version
}

# 5. Secrets Manager — stores DB credentials securely
resource "aws_secretsmanager_secret" "aurora" {
  name                    = "litigation-db-credentials"
  recovery_window_in_days = 0
}

resource "aws_secretsmanager_secret_version" "aurora" {
  secret_id = aws_secretsmanager_secret.aurora.id
  secret_string = jsonencode({
    username = aws_rds_cluster.aurora.master_username
    password = random_password.aurora.result
    host     = aws_rds_cluster.aurora.endpoint
    port     = aws_rds_cluster.aurora.port
    dbname   = aws_rds_cluster.aurora.database_name
  })
}
