#!/bin/bash

# Build Lambda deployment package using Docker
# This ensures lxml and other dependencies work correctly in Lambda

set -e

# Configuration
FUNCTION_NAME="document-review"
DOCKER_IMAGE="one-l-lambda-builder"
OUTPUT_DIR="./build"

echo "Building Lambda deployment package for ${FUNCTION_NAME}..."

# Create output directory
mkdir -p ${OUTPUT_DIR}

# Build Docker image (force x86_64 platform for Lambda compatibility)
echo "Building Docker image with Lambda-compatible environment..."
docker build --platform linux/amd64 -t ${DOCKER_IMAGE} -f Dockerfile.lambda .

# Run container to build deployment package
echo "Creating deployment package in Lambda-compatible environment..."
docker run --rm \
    -v $(pwd)/${OUTPUT_DIR}:/output \
    ${DOCKER_IMAGE}

# Check if package was created
if [ -f "${OUTPUT_DIR}/lambda-deployment.zip" ]; then
    echo "Deployment package created successfully"
    echo "Location: ${OUTPUT_DIR}/lambda-deployment.zip"
    echo "Package size: $(du -h ${OUTPUT_DIR}/lambda-deployment.zip | cut -f1)"
    
    # Show package contents (first 20 files)
    echo "Package contents (sample):"
    unzip -l ${OUTPUT_DIR}/lambda-deployment.zip | head -20
    
    echo ""
    echo "Next steps:"
    echo "1. Deploy with: cdk deploy"
    echo "2. The lxml import error should be resolved"
else
    echo "Failed to create deployment package"
    exit 1
fi 