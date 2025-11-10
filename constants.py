# Configuration constants for the One-L application
# Users can modify these values before deployment

# Stack name for the development CDK deployment
STACK_NAME = "OneL-DV2"

# Cognito domain name for authentication
COGNITO_DOMAIN_NAME = "one-l-auth-dv2"

# Google Document AI Configuration (optional - for better PDF to DOCX conversion)
# If not set, the system will fall back to PyMuPDF conversion
# To get these values:
# 1. Create a Google Cloud project at https://console.cloud.google.com
# 2. Enable Document AI API
# 3. Create a Document AI processor (Form Parser or Document OCR)
# 4. Get the processor ID from the processor details page
GOOGLE_CLOUD_PROJECT_ID = "intense-subject-477818-h6"  # Your Google Cloud project ID
GOOGLE_DOCUMENT_AI_PROCESSOR_ID = "51f27557dca2483d"  # Your Form Parser processor ID
GOOGLE_DOCUMENT_AI_LOCATION = "us"  # Region where processor was created 