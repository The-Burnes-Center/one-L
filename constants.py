# Configuration constants for the One-L application
# Users can modify these values before deployment

# Stack name for the production CDK deployment
STACK_NAME = "OneL-v2"

# Cognito domain name for authentication
COGNITO_DOMAIN_NAME = "onel-v2-one-l-auth"

# Google Document AI Configuration - DISABLED
# The system now uses PyMuPDF for PDF to DOCX conversion
# Google Document AI code is saved in tools_pymupdf_conversion_backup.py for future reference
# GOOGLE_CLOUD_PROJECT_ID = "intense-subject-477818-h6"
# GOOGLE_DOCUMENT_AI_PROCESSOR_ID = "51f27557dca2483d"
# GOOGLE_DOCUMENT_AI_LOCATION = "us"
