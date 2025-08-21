#!/bin/bash
set -e

# =========================================
# Deployment Account Setup
# =========================================

CONFIG_FILE="dogsvscats/dogsvscats-dns-config.json"

if [ ! -f "$CONFIG_FILE" ]; then
    echo "❌ DNS configuration file not found: $CONFIG_FILE"
    echo "Please obtain this file from the master DNS account setup"
    exit 1
fi

# Load configuration
MASTER_ACCOUNT_ID=$(jq -r '.masterAccountId' $CONFIG_FILE)
HOSTED_ZONE_ID=$(jq -r '.hostedZoneId' $CONFIG_FILE)
DOMAIN=$(jq -r '.domain' $CONFIG_FILE)
CROSS_ACCOUNT_ROLE_ARN=$(jq -r '.crossAccountRoleArn' $CONFIG_FILE)

CURRENT_ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
REGION=$(aws configure get region || echo "us-west-2")

echo "Setting up deployment account for DOGSVSCATS"
echo "Current Account: $CURRENT_ACCOUNT_ID"
echo "Master DNS Account: $MASTER_ACCOUNT_ID"
echo "Region: $REGION"
echo "Domain: $DOMAIN"

# =========================================
# Get EKS OIDC provider ID
# =========================================

echo "Getting EKS OIDC provider ID..."
CLUSTER_NAME="krmops-on-eks"

# Get the OIDC issuer URL from the EKS cluster
OIDC_ISSUER=$(aws eks describe-cluster --name $CLUSTER_NAME --region $REGION --query 'cluster.identity.oidc.issuer' --output text 2>/dev/null)

if [ -z "$OIDC_ISSUER" ]; then
    echo "⚠️  Could not retrieve OIDC issuer for cluster $CLUSTER_NAME"
    echo "This is normal if the cluster doesn't exist yet. Using placeholder for now."
    OIDC_PROVIDER_ID="PLACEHOLDER"
else
    # Extract the OIDC provider ID from the issuer URL
    OIDC_PROVIDER_ID=$(echo $OIDC_ISSUER | sed 's|https://oidc.eks.'$REGION'.amazonaws.com/id/||')
    echo "✅ Found OIDC Provider ID: $OIDC_PROVIDER_ID"
fi

# =========================================
# Create IAM role for ACM certificate validation
# =========================================

echo "Creating IAM role for ACM certificate validation..."

# Trust policy for EKS service account
cat > acm-cert-trust-policy.json << EOF
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Principal": {
                "Federated": "arn:aws:iam::$CURRENT_ACCOUNT_ID:oidc-provider/oidc.eks.$REGION.amazonaws.com/id/$OIDC_PROVIDER_ID"
            },
            "Action": "sts:AssumeRoleWithWebIdentity",
            "Condition": {
                "StringEquals": {
                    "oidc.eks.$REGION.amazonaws.com/id/$OIDC_PROVIDER_ID:sub": "system:serviceaccount:ack-system:ack-acm-controller",
                    "oidc.eks.$REGION.amazonaws.com/id/$OIDC_PROVIDER_ID:aud": "sts.amazonaws.com"
                }
            }
        }
    ]
}
EOF

# ACM and Route 53 permissions
cat > acm-cert-policy.json << EOF
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Action": [
                "acm:*",
                "route53:GetChange",
                "route53:ChangeResourceRecordSets",
                "route53:ListResourceRecordSets"
            ],
            "Resource": "*"
        },
        {
            "Effect": "Allow",
            "Action": "sts:AssumeRole",
            "Resource": "$CROSS_ACCOUNT_ROLE_ARN"
        }
    ]
}
EOF

# Create role (this will be updated with correct OIDC provider after EKS cluster exists)
aws iam create-role \
    --role-name externalACMCertificate \
    --assume-role-policy-document file://acm-cert-trust-policy.json \
    --description "Role for ACM certificate validation with cross-account DNS" || echo "Role may already exist"

aws iam put-role-policy \
    --role-name externalACMCertificate \
    --policy-name ACMCertificatePolicy \
    --policy-document file://acm-cert-policy.json

echo "✅ ACM certificate role created"

# =========================================
# Generate unique subdomain preview
# =========================================

UNIQUE_SUBDOMAIN="workshop-${CURRENT_ACCOUNT_ID: -4}-${REGION}"
VOTE_DOMAIN="vote.${UNIQUE_SUBDOMAIN}.${DOMAIN}"
RESULTS_DOMAIN="results.${UNIQUE_SUBDOMAIN}.${DOMAIN}"

echo "Generated unique subdomain: $UNIQUE_SUBDOMAIN"
echo "Vote app will be available at: https://$VOTE_DOMAIN"
echo "Results app will be available at: https://$RESULTS_DOMAIN"
echo ""
echo "ℹ️  Note: The actual subdomain configuration will be handled automatically"
echo "   by the workshopbuild.sh script using the update_yaml.py logic."

# Cleanup
rm -f acm-cert-trust-policy.json acm-cert-policy.json

echo ""
echo "🎉 Deployment account setup complete!"
echo ""
echo "Your unique domains:"
echo "  Vote: https://$VOTE_DOMAIN"
echo "  Results: https://$RESULTS_DOMAIN"
echo ""
echo "Next: Run the workshop build script to deploy your stack"