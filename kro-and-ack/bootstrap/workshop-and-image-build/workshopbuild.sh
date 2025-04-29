#!/bin/bash
set -e  # Exit immediately if a command fails

# =========================================
# Install and start Docker
# =========================================

sudo yum install -y docker

sudo service docker start

# =========================================
# Retrieve Token for IMDSv2 and Instance Identity Document
# =========================================

# Get the IMDSv2 token
TOKEN=$(curl -s -X PUT "http://169.254.169.254/latest/api/token" -H "X-aws-ec2-metadata-token-ttl-seconds: 21600")

if [ -z "$TOKEN" ]; then
    echo "Error: Unable to retrieve IMDSv2 token."
    exit 1
fi

# Use the token to get the instance identity document
INSTANCE_IDENTITY=$(curl -s -H "X-aws-ec2-metadata-token: $TOKEN" http://169.254.169.254/latest/dynamic/instance-identity/document)
if [[ -z "$INSTANCE_IDENTITY" ]]; then
    echo "Error: Unable to retrieve instance identity document."
    exit 1
fi

echo "Instance identity document retrieved successfully."
echo "$INSTANCE_IDENTITY"

# Extract Account ID and Region using jq
ACCOUNT_ID=$(echo "$INSTANCE_IDENTITY" | jq -r '.accountId')
REGION=$(echo "$INSTANCE_IDENTITY" | jq -r '.region')

if [[ -z "$ACCOUNT_ID" || -z "$REGION" ]]; then
    echo "Error: Failed to extract AWS account ID or region."
    exit 1
fi

echo "Detected AWS Account ID: $ACCOUNT_ID"
echo "Detected Region: $REGION"


# =========================================
# Set Variables for Image and Repository
# =========================================

# Name for your local Docker image and the ECR repository
S3_IMAGE_NAME="s3-app"         # Change as appropriate
RDS_IMAGE_NAME="rds-app"         # Change as appropriate
REPO_NAME="krmops-ecr-repo"       # Change as appropriate

# =========================================
# Build Docker Image
# =========================================

echo "Building Docker image: ${S3_IMAGE_NAME}:latest"
sudo docker build -t ${S3_IMAGE_NAME}:latest /home/ec2-user/environment/krmops-on-eks/krmops-on-eks-workshop/application/s3-demo-app/.

echo "Building Docker image: ${RDS_IMAGE_NAME}:latest"
sudo docker build -t ${RDS_IMAGE_NAME}:latest /home/ec2-user/environment/krmops-on-eks/krmops-on-eks-workshop/application/rds-demo-app/.

# =========================================
# Create an ECR Repository (if it doesn't exist)
# =========================================

echo "Checking if ECR repository '${REPO_NAME}' exists..."
if aws ecr describe-repositories --repository-names ${REPO_NAME} --region ${REGION} >/dev/null 2>&1; then
    echo "ECR repository '${REPO_NAME}' already exists."
else
    echo "Creating ECR repository '${REPO_NAME}'..."
    aws ecr create-repository --repository-name ${REPO_NAME} --region ${REGION}
fi

# =========================================
# Authenticate Docker to AWS ECR
# =========================================

echo "Authenticating Docker to ECR..."
aws ecr get-login-password --region ${REGION} | sudo docker login --username AWS --password-stdin ${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com

# =========================================
# Tag and Push Docker Image to ECR
# =========================================

# Define the full image URI for ECR
ECR_IMAGE_URI="${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com/${REPO_NAME}"

echo "Tagging the image as ${ECR_IMAGE_URI}:s3-latest"
sudo docker tag ${S3_IMAGE_NAME}:latest ${ECR_IMAGE_URI}:s3-latest

echo "Tagging the image as ${ECR_IMAGE_URI}:rds-latest"
sudo docker tag ${RDS_IMAGE_NAME}:latest ${ECR_IMAGE_URI}:rds-latest

echo "Pushing the image to ECR..."
sudo docker push ${ECR_IMAGE_URI}:s3-latest
sudo docker push ${ECR_IMAGE_URI}:rds-latest

echo "Docker image has been successfully built and pushed to ECR."

# =========================================
# replace variables fields on the rgd's
# ========================================
yes | sudo dnf install python3-pip

sudo pip3 install boto3

python3 /home/ec2-user/environment/krmops-on-eks/krmops-on-eks-workshop/kro-and-ack/bootstrap/workshop-and-image-build/update_yaml.py rdsinstance/rg.yaml \
    /home/ec2-user/environment/krmops-on-eks/kro/podidenity/rg.yaml \
    rdswebstack/instance.yaml \
    /home/ec2-user/environment/krmops-on-eks/kro/webstack/instance-tmpl.yaml \
    /home/ec2-user/environment/krmops-on-eks/kro/webapp/rg.yaml \
    --region ${REGION} \
    --cluster krmops-on-eks \
    --ecr-repo-uri ${ECR_IMAGE_URI} \
    --ecr-tag rds-latest \
    --web-tag s3-latest

cp -R rdsinstance /home/ec2-user/environment/krmops-on-eks/kro
cp -R rdswebstack /home/ec2-user/environment/krmops-on-eks/kro
cp -R webapprds /home/ec2-user/environment/krmops-on-eks/kro
cp -R s3adopt /home/ec2-user/environment/krmops-on-eks/kro

# ========================================
# Find the first IAM role that starts with "ack-rds-"
# ========================================
echo "Finding IAM role starting with 'ack-rds-'..."
ROLE_NAME=$(aws iam list-roles --query "Roles[?starts_with(RoleName, 'ack-rds-')].RoleName" --output text | head -n 1)

if [ -z "$ROLE_NAME" ]; then
    echo "No role starting with 'ack-rds-' was found."
    exit 1
fi

echo "Found role: $ROLE_NAME"

# ========================================
# Create a policy document for KMS permissions
# ========================================
echo "Creating KMS policy document..."
cat > kms-policy.json << EOF
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Action": [
                "kms:Decrypt",
                "kms:DescribeKey",
                "kms:Encrypt",
                "kms:GenerateDataKey*",
                "kms:ReEncrypt*"
            ],
            "Resource": "*"
        }
    ]
}
EOF

# ========================================
# Create a policy document for Secrets Manager permissions
# ========================================
echo "Creating Secrets Manager policy document..."
cat > secretsmanager-policy.json << EOF
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Action": [
                "secretsmanager:GetResourcePolicy",
                "secretsmanager:GetSecretValue",
                "secretsmanager:DescribeSecret",
                "secretsmanager:ListSecretVersionIds",
                "secretsmanager:CreateSecret",
                "secretsmanager:PutSecretValue",
                "secretsmanager:GetSecretValue",
                "secretsmanager:UpdateSecret",
                "secretsmanager:DeleteSecret",
                "secretsmanager:TagResource"
            ],
            "Resource": "*"
        }
    ]
}
EOF

# ========================================
# Create the policies in AWS
# ========================================
echo "Creating KMS policy in AWS..."
KMS_POLICY_ARN=$(aws iam create-policy \
    --policy-name "${ROLE_NAME}-kms-policy" \
    --policy-document file://kms-policy.json \
    --query 'Policy.Arn' \
    --output text)

echo "Creating Secrets Manager policy in AWS..."
SM_POLICY_ARN=$(aws iam create-policy \
    --policy-name "${ROLE_NAME}-secretsmanager-policy" \
    --policy-document file://secretsmanager-policy.json \
    --query 'Policy.Arn' \
    --output text)

# ========================================
# Attach the policies to the role
# ========================================
echo "Attaching KMS policy to role..."
aws iam attach-role-policy \
    --role-name "$ROLE_NAME" \
    --policy-arn "$KMS_POLICY_ARN"

echo "Attaching Secrets Manager policy to role..."
aws iam attach-role-policy \
    --role-name "$ROLE_NAME" \
    --policy-arn "$SM_POLICY_ARN"
# ========================================
# Clean up temporary files
# ========================================
rm -f kms-policy.json secretsmanager-policy.json

echo "Successfully added KMS and Secrets Manager permissions to role: $ROLE_NAME"
echo "KMS Policy ARN: $KMS_POLICY_ARN"
echo "Secrets Manager Policy ARN: $SM_POLICY_ARN"

# ========================================
# Install secret provider
# ========================================

 su ec2-user -c 'helm repo add secrets-store-csi-driver https://kubernetes-sigs.github.io/secrets-store-csi-driver/charts'
 su ec2-user -c 'helm install -n kube-system csi-secrets-store secrets-store-csi-driver/secrets-store-csi-driver'

 su ec2-user -c 'kubectl apply -f \
  https://raw.githubusercontent.com/aws/secrets-store-csi-driver-provider-aws/main/deployment/aws-provider-installer.yaml'


# ========================================
# Apply ingress class
# ========================================

su ec2-user -c 'kubectl apply -f ingress/ingressClass.yaml'
su ec2-user -c 'kubectl apply -f ingress/ingressClassParams.yaml'

# ========================================
# S3 adopt bucket creation
# ========================================

export BUCKET_NAME=krmops-s3adopt-$ACCOUNT_ID
aws s3 mb s3://$BUCKET_NAME --region $REGION