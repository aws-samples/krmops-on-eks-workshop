#!/bin/bash
set -e

# =========================================
# Master DNS Account Setup (Run Once)
# =========================================

DOMAIN="dogsvscats.us"
MASTER_ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)

echo "Setting up master DNS for domain: $DOMAIN"
echo "Master Account ID: $MASTER_ACCOUNT_ID"

# =========================================
# Create Route 53 Hosted Zone
# =========================================

echo "Creating Route 53 hosted zone for $DOMAIN..."
HOSTED_ZONE_ID=$(aws route53 create-hosted-zone \
    --name $DOMAIN \
    --caller-reference $(date +%s) \
    --hosted-zone-config Comment="Master DNS for CATSVSDOGS multi-account deployments" \
    --query 'HostedZone.Id' \
    --output text | sed 's|/hostedzone/||')

echo "Created hosted zone: $HOSTED_ZONE_ID"

# Get name servers
echo "Getting Route 53 name servers..."
aws route53 get-hosted-zone --id $HOSTED_ZONE_ID \
    --query 'DelegationSet.NameServers' \
    --output table

echo ""
echo "🚨 IMPORTANT: Update your GoDaddy DNS settings with these Route 53 name servers!"
echo ""

# =========================================
# Create Cross-Account IAM Role
# =========================================

echo "Creating cross-account IAM role for DNS management..."

# Trust policy for cross-account access
cat > dns-trust-policy.json << EOF
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Principal": {
                "AWS": "*"
            },
            "Action": "sts:AssumeRole",
            "Condition": {
                "StringEquals": {
                    "sts:ExternalId": "dogsvscats-dns-access"
                }
            }
        }
    ]
}
EOF

# DNS management policy
cat > dns-management-policy.json << EOF
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Action": [
                "route53:ChangeResourceRecordSets",
                "route53:GetChange",
                "route53:ListResourceRecordSets",
                "route53:GetHostedZone"
            ],
            "Resource": [
                "arn:aws:route53:::hostedzone/$HOSTED_ZONE_ID",
                "arn:aws:route53:::change/*"
            ]
        },
        {
            "Effect": "Allow",
            "Action": [
                "route53:ListHostedZones",
                "route53:ListHostedZonesByName"
            ],
            "Resource": "*"
        }
    ]
}
EOF

# Create role
aws iam create-role \
    --role-name CrossAccountDNSRole \
    --assume-role-policy-document file://dns-trust-policy.json \
    --description "Cross-account role for CATSVSDOGS DNS management"

# Attach policy
aws iam put-role-policy \
    --role-name CrossAccountDNSRole \
    --policy-name DNSManagementPolicy \
    --policy-document file://dns-management-policy.json

echo "✅ Cross-account DNS role created: arn:aws:iam::$MASTER_ACCOUNT_ID:role/CrossAccountDNSRole"

# =========================================
# Create configuration file
# =========================================

cat > dogsvscats-dns-config.json << EOF
{
    "domain": "$DOMAIN",
    "hostedZoneId": "$HOSTED_ZONE_ID",
    "masterAccountId": "$MASTER_ACCOUNT_ID",
    "crossAccountRoleArn": "arn:aws:iam::$MASTER_ACCOUNT_ID:role/CrossAccountDNSRole",
    "externalId": "dogsvscats-dns-access"
}
EOF

echo "✅ DNS configuration saved to: dogsvscats-dns-config.json"

# Cleanup
rm -f dns-trust-policy.json dns-management-policy.json

echo ""
echo "🎉 Master DNS setup complete!"
echo ""
echo "Next steps:"
echo "1. Update GoDaddy nameservers with the Route 53 nameservers shown above"
echo "2. Share the dogsvscats-dns-config.json file with deployment accounts"
echo "3. Run the workshop build script in deployment accounts"