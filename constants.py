# Configuration constants for the One-L application
# Users can modify these values before deployment

# Stack name for CDK deployment
# Dev branch: "OneL-DV2"
# Main/prod branch: "OneL-v2"
STACK_NAME = "OneL-DV2"

# Cognito domain name for authentication
# Dev branch: "one-l-auth-dv2"
# Main/prod branch: "one-l-auth" (or appropriate prod domain)
COGNITO_DOMAIN_NAME = "one-l-auth-dv2"

# Document Chunking Configuration
# Character-based chunking configuration
CHUNK_SIZE_CHARACTERS = 30000  # Chunk size in characters
CHUNK_OVERLAP_CHARACTERS = 2000  # Overlap between chunks
