# Configuration constants for the One-L application
# These values are determined by environment variables (set by GitHub Actions)
# or fall back to dev defaults for local development

import os

# Stack name for CDK deployment
# Set STACK_NAME environment variable in GitHub Actions:
#   - Dev: "OneL-DV2"
#   - Production/Main: "OneL-Prod"
STACK_NAME = os.environ.get("STACK_NAME", "OneL-DV2")

# Cognito domain name for authentication
# Set COGNITO_DOMAIN_NAME environment variable in GitHub Actions:
#   - Dev: "one-l-auth-dv2"
#   - Production/Main: "one-l-auth"
COGNITO_DOMAIN_NAME = os.environ.get("COGNITO_DOMAIN_NAME", "one-l-auth-dv2")

# Document Chunking Configuration
# Character-based chunking configuration
CHUNK_SIZE_CHARACTERS = int(os.environ.get("CHUNK_SIZE_CHARACTERS", "30000"))  # Chunk size in characters
CHUNK_OVERLAP_CHARACTERS = int(os.environ.get("CHUNK_OVERLAP_CHARACTERS", "2000"))  # Overlap between chunks
