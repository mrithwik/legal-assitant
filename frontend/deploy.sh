#!/bin/bash
set -e

AWS_REGION="us-east-2"
AWS_ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
ECR_URL="$AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/litigation-frontend"

if [ -z "$NEXT_PUBLIC_API_URL" ]; then
  echo "Error: NEXT_PUBLIC_API_URL is not set"
  echo "Usage: NEXT_PUBLIC_API_URL=https://... NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY=pk_test_... ./deploy.sh"
  exit 1
fi

if [ -z "$NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY" ]; then
  echo "Error: NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY is not set"
  echo "Usage: NEXT_PUBLIC_API_URL=https://... NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY=pk_test_... ./deploy.sh"
  exit 1
fi

echo "Authenticating Docker to ECR..."
aws ecr get-login-password --region $AWS_REGION \
  | docker login --username AWS --password-stdin $ECR_URL

echo "Building frontend image..."
docker build \
  --build-arg NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL \
  --build-arg NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY=$NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY \
  --platform linux/amd64 \
  -t litigation-frontend .

echo "Tagging image..."
docker tag litigation-frontend:latest $ECR_URL:latest

echo "Pushing image to ECR..."
docker push $ECR_URL:latest

echo "Done. App Runner will detect the new image and redeploy automatically."
