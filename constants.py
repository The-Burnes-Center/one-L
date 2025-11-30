# Configuration constants for the One-L application
# Users can modify these values before deployment

# Stack name for the production CDK deployment
STACK_NAME = "OneL-DV2"

# Cognito domain name for authentication
COGNITO_DOMAIN_NAME = "onel-v2-one-l-auth"



# Document Chunking Configuration
# Character-based chunking configuration
CHUNK_SIZE_CHARACTERS = 100000  # Default chunk size in characters
CHUNK_OVERLAP_CHARACTERS = 5000  # Overlap between chunks
