#!/bin/bash

# Build script for One-L React Application
# This script builds the React app for deployment

set -e

echo "Building One-L React Application..."

# Navigate to the user interface directory
cd "$(dirname "$0")"

# Install dependencies
echo "Installing dependencies..."
npm install

# Build the React app
echo "Building React app..."
npm run build

# Ensure the build directory exists
if [ ! -d "build" ]; then
    echo "Build directory not found. Creating..."
    mkdir -p build
fi

# Copy any additional assets
echo "Copying additional assets..."
cp -r public/favicon.ico build/ 2>/dev/null || true

echo "Build completed successfully!"
echo "Build output is in the 'build' directory" 