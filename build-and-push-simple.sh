#!/bin/bash

# Simple Finch Build and Push Script (single platform)
# Faster build for local development using Finch

set -e

DOCKERHUB_USERNAME="${DOCKERHUB_USERNAME:-YOUR_DOCKERHUB_USERNAME}"
TAG="${TAG:-latest}"

# Colors
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

print_status() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

# Check if Finch is available
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
    print_error "Please set DOCKERHUB_USERNAME environment variable"
    exit 1
fi

BASE_DIR="application/dogsvscats/voting-app"

# Vote app
print_status "Building and pushing vote app..."
finch build -t "$DOCKERHUB_USERNAME/vote:$TAG" --target final "$BASE_DIR/vote"
finch push "$DOCKERHUB_USERNAME/vote:$TAG"

# Result app  
print_status "Building and pushing result app..."
finch build -t "$DOCKERHUB_USERNAME/result:$TAG" "$BASE_DIR/result"
finch push "$DOCKERHUB_USERNAME/result:$TAG"

# Worker app
print_status "Building and pushing worker app..."
finch build -t "$DOCKERHUB_USERNAME/worker:$TAG" "$BASE_DIR/worker"
finch push "$DOCKERHUB_USERNAME/worker:$TAG"

print_status "All images pushed successfully!"
print_status "Images: $DOCKERHUB_USERNAME/{vote,result,worker}:$TAG"