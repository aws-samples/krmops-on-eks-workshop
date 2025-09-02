#!/bin/bash

# Dogs vs Cats Docker Build and Push Script
# This script builds and pushes all three components of the voting app to Docker Hub

set -e  # Exit on any error

# Configuration
DOCKERHUB_USERNAME="${DOCKERHUB_USERNAME:-YOUR_DOCKERHUB_USERNAME}"
TAG="${TAG:-latest}"
PLATFORM="${PLATFORM:-linux/amd64,linux/arm64}"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Function to print colored output
print_status() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Check if Finch is available and running
if ! command -v finch &> /dev/null; then
    print_error "Finch is not installed. Please install Finch and try again."
    print_error "Visit: https://github.com/runfinch/finch"
    exit 1
fi

if ! finch info > /dev/null 2>&1; then
    print_error "Finch is not running. Please start Finch and try again."
    print_error "Run: finch vm start"
    exit 1
fi

# Check if logged into Docker Hub
if ! finch info | grep -q "Username"; then
    print_warning "You may not be logged into Docker Hub. Run 'finch login' if needed."
fi

# Validate username
if [ "$DOCKERHUB_USERNAME" = "YOUR_DOCKERHUB_USERNAME" ]; then
    print_error "Please set your Docker Hub username:"
    print_error "  export DOCKERHUB_USERNAME=your-username"
    print_error "  or edit this script to replace YOUR_DOCKERHUB_USERNAME"
    exit 1
fi

print_status "Building and pushing Dogs vs Cats images to Docker Hub"
print_status "Username: $DOCKERHUB_USERNAME"
print_status "Tag: $TAG"
print_status "Platform: $PLATFORM"

# Base directory
BASE_DIR="application/dogsvscats/voting-app"

# Build and push vote app (Python Flask)
print_status "Building vote app..."
cd "$BASE_DIR/vote"
finch build --platform "$PLATFORM" \
    -t "$DOCKERHUB_USERNAME/vote:$TAG" \
    --target final .
finch push "$DOCKERHUB_USERNAME/vote:$TAG"
print_status "✓ Vote app pushed successfully"

# Build and push result app (Node.js)
print_status "Building result app..."
cd "../result"
finch build --platform "$PLATFORM" \
    -t "$DOCKERHUB_USERNAME/result:$TAG" .
finch push "$DOCKERHUB_USERNAME/result:$TAG"
print_status "✓ Result app pushed successfully"

# Build and push worker app (.NET Core)
print_status "Building worker app..."
cd "../worker"
finch build --platform "$PLATFORM" \
    -t "$DOCKERHUB_USERNAME/worker:$TAG" .
finch push "$DOCKERHUB_USERNAME/worker:$TAG"
print_status "✓ Worker app pushed successfully"

# Return to original directory
cd - > /dev/null

print_status "All images built and pushed successfully!"
print_status ""
print_status "Your images are now available at:"
print_status "  - $DOCKERHUB_USERNAME/vote:$TAG"
print_status "  - $DOCKERHUB_USERNAME/result:$TAG"
print_status "  - $DOCKERHUB_USERNAME/worker:$TAG"