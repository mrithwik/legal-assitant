variable "aws_region" {
  description = "AWS region to deploy resources"
  type        = string
  default     = "us-east-2"
}

variable "project_name" {
  description = "Project name for tagging and naming"
  type        = string
  default     = "litigation-prep-assistant"
}

variable "clerk_secret_key" {
  description = "Clerk secret key for server-side authentication"
  type        = string
  sensitive   = true
}

variable "next_public_api_url" {
  description = "Backend API URL baked into the Next.js bundle at image build time"
  type        = string
}

variable "next_public_clerk_publishable_key" {
  description = "Clerk publishable key baked into the Next.js bundle at image build time"
  type        = string
}
