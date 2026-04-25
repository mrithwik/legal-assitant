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

variable "openai_api_key" {
  description = "OpenAI API key"
  type        = string
  sensitive   = true
}

variable "openrouter_api_key" {
  description = "Optional OpenRouter API key"
  type        = string
  sensitive   = true
  default     = ""
}

variable "openai_model" {
  description = "OpenAI model to use"
  type        = string
  default     = "gpt-4o"
}

variable "clerk_jwks_url" {
  description = "Clerk JWKS URL for JWT validation"
  type        = string
}

variable "clerk_issuer" {
  description = "Clerk issuer URL"
  type        = string
}

variable "allowed_origins" {
  description = "Allowed CORS origins"
  type        = string
}

variable "pinecone_api_key" {
  description = "Pinecone API key for RAG"
  type        = string
  sensitive   = true
  default     = ""
}

variable "pinecone_index_host" {
  description = "Pinecone index host URL"
  type        = string
  default     = ""
}

variable "pinecone_index_name" {
  description = "Pinecone index name"
  type        = string
  default     = ""
}

variable "pinecone_namespace" {
  description = "Pinecone namespace"
  type        = string
  default     = ""
}

variable "langfuse_public_key" {
  description = "Langfuse public key for observability"
  type        = string
  default     = ""
}

variable "langfuse_secret_key" {
  description = "Langfuse secret key"
  type        = string
  sensitive   = true
  default     = ""
}

variable "langfuse_host" {
  description = "Langfuse host URL"
  type        = string
  default     = "https://cloud.langfuse.com"
}
