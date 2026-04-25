#!/bin/bash
set -e

AWS_REGION="us-east-2"
AWS_ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
ECR_URL="$AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/litigation-backend"

echo "Authenticating Docker to ECR..."
aws ecr get-login-password --region $AWS_REGION \
  | docker login --username AWS --password-stdin $ECR_URL

echo "Building backend image..."
docker build --platform linux/amd64 -t litigation-backend .

echo "Tagging image..."
docker tag litigation-backend:latest $ECR_URL:latest

echo "Pushing image to ECR..."
docker push $ECR_URL:latest

echo "Done. App Runner will detect the new image and redeploy automatically."
