# 🌐 Multi-Account DOGSVSCATS Domain Deployment Guide

This guide provides a seamless approach to deploy the Dogs vs Cats voting app across multiple AWS accounts using your GoDaddy domain `dogsvscats.us`.

## 🏗️ Architecture Overview

```
Master DNS Account (Route 53)
├── dogsvscats.us (Hosted Zone)
├── Cross-account IAM roles
└── Centralized DNS management

Deployment Account 1
├── workshop-1234-us-west-2.dogsvscats.us
├── vote.workshop-1234-us-west-2.dogsvscats.us
└── results.workshop-1234-us-west-2.dogsvscats.us

Deployment Account 2
├── workshop-5678-eu-west-1.dogsvscats.us
├── vote.workshop-5678-eu-west-1.dogsvscats.us
└── results.workshop-5678-eu-west-1.dogsvscats.us
```

## 🚀 Quick Start

### Step 1: Master DNS Setup (One Time Only)

**Run this in your main AWS account where you want to manage DNS:**

```bash
# 1. Run the master DNS setup script
./setup-master-dns.sh

# 2. Update GoDaddy nameservers with the Route 53 nameservers provided
# 3. Share the generated dogsvscats-dns-config.json with deployment accounts
```

### Step 2: Deployment Account Setup (Per Account)

**Run this in each AWS account where you want to deploy the voting app:**

```bash
# 1. Copy dogsvscats-dns-config.json to this account
# 2. Run the deployment account setup
./setup-deployment-account.sh

# 3. Run the workshop build script
./workshopbuild.sh
```

## 📋 Detailed Steps

### 🎯 Master DNS Account Setup

1. **Choose your master DNS account** (typically your main AWS account)
2. **Run the setup script:**
   ```bash
   ./setup-master-dns.sh
   ```
3. **Update GoDaddy DNS:**
   - Log into GoDaddy
   - Go to DNS management for dogsvscats.us
   - Replace the nameservers with the Route 53 nameservers provided by the script
4. **Share configuration:**
   - The script creates `dogsvscats-dns-config.json`
   - Share this file with all deployment accounts

### 🚀 Deployment Account Setup

For each AWS account where you want to deploy:

1. **Copy the DNS config file:**
   ```bash
   # Copy dogsvscats-dns-config.json to the dogsvscats/ directory
   # The file should be located at: dogsvscats/dogsvscats-dns-config.json
   ```

2. **Run deployment setup:**
   ```bash
   ./setup-deployment-account.sh
   ```

3. **Deploy the voting app:**
   ```bash
   ./workshopbuild.sh
   ```

## 🌟 What You Get

### Automatic Features:

- **Unique subdomains** per account: `workshop-{account-id-suffix}-{region}.dogsvscats.us`
- **SSL certificates** automatically provisioned via ACM
- **DNS validation** handled automatically
- **HTTPS redirect** from HTTP
- **Cross-account DNS** management

### Example Deployments:

| Account | Region | Vote URL | Results URL |
|---------|--------|----------|-------------|
| 123456789012 | us-west-2 | https://vote.workshop-9012-us-west-2.dogsvscats.us | https://results.workshop-9012-us-west-2.dogsvscats.us |
| 987654321098 | eu-west-1 | https://vote.workshop-1098-eu-west-1.dogsvscats.us | https://results.workshop-1098-eu-west-1.dogsvscats.us |

## 🔧 Advanced Configuration

### Custom Subdomains

To use custom subdomains instead of auto-generated ones:

```bash
# Edit dogsvscats/voting-app-instance.yaml before running workshopbuild.sh
spec:
  domain:
    subdomain: "my-custom-name"  # Instead of auto-generated
```

### Multiple Regions in Same Account

Deploy to multiple regions in the same account:

```bash
# Deploy to us-west-2
AWS_DEFAULT_REGION=us-west-2 ./workshopbuild.sh

# Deploy to eu-west-1  
AWS_DEFAULT_REGION=eu-west-1 ./workshopbuild.sh
```

## 🛠️ Troubleshooting

### DNS Propagation

- DNS changes can take up to 48 hours to propagate globally
- Use `dig` or `nslookup` to check DNS resolution
- Test from different locations/networks

### Certificate Validation

- ACM certificates validate automatically via DNS
- Check ACM console for validation status
- Ensure Route 53 has proper permissions

### Cross-Account Access

If you get permission errors:
1. Verify the master DNS account setup completed successfully
2. Check that the `dogsvscats-dns-config.json` file is correct
3. Ensure the deployment account has the correct IAM roles

### Common Commands

```bash
# Check DNS resolution
dig vote.workshop-1234-us-west-2.dogsvscats.us

# Check certificate status
aws acm list-certificates --region us-west-2

# Check Route 53 records
aws route53 list-resource-record-sets --hosted-zone-id Z1234567890ABC

# Check Kro resources
kubectl get votingapp -A
kubectl get certificate -A
kubectl get ingress -A
```

## 🎉 Benefits

✅ **Seamless multi-account deployment**
✅ **Automatic SSL certificates**
✅ **Unique domains per deployment**
✅ **Centralized DNS management**
✅ **No manual certificate management**
✅ **Production-ready HTTPS**
✅ **Easy cleanup per account**

## 🧹 Cleanup

To remove a deployment:

```bash
# Delete the Kro resources
kubectl delete votingapp dogsvscats-voting-app

# The DNS records and certificates will be cleaned up automatically
```

To remove the master DNS setup:

```bash
# Delete the Route 53 hosted zone (only if no longer needed)
aws route53 delete-hosted-zone --id Z1234567890ABC

# Update GoDaddy back to original nameservers
```

---

This approach gives you a production-ready, scalable solution for deploying the Dogs vs Cats voting app across multiple AWS accounts with proper SSL certificates and custom domains! 🐕🐱