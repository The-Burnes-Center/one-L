# One-L Deployment Guide

## Overview

One-L uses a **branch-based deployment strategy** with two environments:

| Environment | Branch | Stack Name | Cognito Domain |
|-------------|--------|------------|----------------|
| Development | `dev` | `OneL-DV2` | `one-l-auth-dv2` |
| Production | `main` | `OneL-Prod` | `one-l-auth-prod` |

Deployments are automated via **GitHub Actions** - pushing to either branch triggers the corresponding deployment.

## Prerequisites

### AWS Account Setup
- **AWS Account** with sufficient permissions for:
  - Lambda, Step Functions, API Gateway, S3, DynamoDB, Cognito
  - Bedrock (Claude 4 Sonnet access)
  - OpenSearch Serverless, CloudFront
- **AWS CLI** configured with appropriate credentials
- **Bedrock Model Access**: Ensure Claude 4 Sonnet is available in your region

### Local Development Environment
- **Python 3.12+** with pip
- **Node.js 20+** with npm
- **AWS CDK v2** (`npm install -g aws-cdk`)
- **Git** for version control

### Required AWS Permissions

Your AWS user/role needs the following managed policies:
- `PowerUserAccess` (recommended for full deployment)
- Or custom policy with these services:
  ```json
  {
    "Version": "2012-10-17",
    "Statement": [
      {
        "Effect": "Allow",
        "Action": [
          "lambda:*",
          "states:*",
          "apigateway:*",
          "s3:*",
          "dynamodb:*",
          "cognito-idp:*",
          "bedrock:*",
          "aoss:*",
          "cloudfront:*",
          "iam:*",
          "logs:*",
          "cloudformation:*"
        ],
        "Resource": "*"
      }
    ]
  }
  ```

## Automated Deployment (GitHub Actions)

### How It Works

1. **Push to `dev` branch** → Deploys to `OneL-DV2` stack
2. **Push to `main` branch** → Deploys to `OneL-Prod` stack

The `constants.py` file uses **environment variables** that are set by GitHub Actions:

```python
# constants.py reads from environment variables
STACK_NAME = os.environ.get("STACK_NAME", "OneL-DV2")  # Default: dev
COGNITO_DOMAIN_NAME = os.environ.get("COGNITO_DOMAIN_NAME", "one-l-auth-dv2")
```

### GitHub Actions Workflows

| Workflow | File | Trigger | Stack |
|----------|------|---------|-------|
| Deploy to Dev | `.github/workflows/deploy.yml` | Push to `dev` | `OneL-DV2` |
| Deploy to Production | `.github/workflows/deploy-production.yml` | Push to `main` | `OneL-Prod` |

### Triggering a Deployment

**Option 1: Push to branch**
```bash
git push origin dev    # Deploys to dev
git push origin main   # Deploys to production
```

**Option 2: Manual trigger via GitHub UI**
1. Go to https://github.com/The-Burnes-Center/one-L/actions
2. Select the workflow (Deploy to Dev or Deploy to Production)
3. Click "Run workflow"

## Local Deployment

### 1. Clone and Setup

```bash
# Clone the repository
git clone <repository-url>
cd one-L

# Create Python virtual environment
python3 -m venv .venv

# Activate virtual environment
source .venv/bin/activate  # Linux/macOS
# .venv\Scripts\activate.bat  # Windows

# Install Python dependencies
pip install -r requirements.txt
```

### 2. Configure Environment

Set environment variables to choose which stack to deploy:

```bash
# For dev deployment
export STACK_NAME="OneL-DV2"
export COGNITO_DOMAIN_NAME="one-l-auth-dv2"

# For production deployment
export STACK_NAME="OneL-Prod"
export COGNITO_DOMAIN_NAME="one-l-auth-prod"

# For a custom stack (testing)
export STACK_NAME="OneL-MyTest"
export COGNITO_DOMAIN_NAME="one-l-auth-mytest"
```

### 3. CDK Bootstrap (One-time Setup)

```bash
# Bootstrap CDK in your AWS account/region
cdk bootstrap

# If deploying to specific account/region
cdk bootstrap aws://ACCOUNT-NUMBER/REGION
```

### 4. Deploy Infrastructure

```bash
# Preview changes
cdk diff

# Generate CloudFormation template
cdk synth

# Deploy stack
cdk deploy --require-approval never
```

**Deployment Time**: Approximately 15-20 minutes for initial deployment.

### 5. Post-Deployment Configuration

After CDK deploy completes, you need to:

1. **Update Cognito OAuth settings** with CloudFront callback URLs
2. **Generate and upload config.json** to the S3 website bucket
3. **Invalidate CloudFront cache**

These steps are automated in GitHub Actions but for local deployment:

```bash
# Get stack outputs
aws cloudformation describe-stacks --stack-name $STACK_NAME \
  --query 'Stacks[0].Outputs[*].{Key:OutputKey,Value:OutputValue}' --output table

# Update Cognito (replace values from outputs above)
aws cognito-idp update-user-pool-client \
  --user-pool-id <USER_POOL_ID> \
  --client-id <CLIENT_ID> \
  --callback-urls "https://<CLOUDFRONT_DOMAIN>" "https://<CLOUDFRONT_DOMAIN>/" \
  --logout-urls "https://<CLOUDFRONT_DOMAIN>" "https://<CLOUDFRONT_DOMAIN>/" \
  --supported-identity-providers "COGNITO" \
  --allowed-o-auth-flows "code" \
  --allowed-o-auth-scopes "openid" "email" "profile" \
  --allowed-o-auth-flows-user-pool-client

# Create config.json and upload to S3
cat > config.json << EOF
{
  "apiGatewayUrl": "<API_GATEWAY_URL>",
  "userPoolId": "<USER_POOL_ID>",
  "userPoolClientId": "<CLIENT_ID>",
  "userPoolDomain": "<COGNITO_DOMAIN_URL>",
  "region": "us-east-1",
  "stackName": "$STACK_NAME",
  "webSocketUrl": "<WEBSOCKET_URL>",
  "callbackUrl": "https://<CLOUDFRONT_DOMAIN>"
}
EOF

aws s3 cp config.json s3://<WEBSITE_BUCKET>/config.json --content-type application/json

# Invalidate CloudFront
aws cloudfront create-invalidation --distribution-id <DISTRIBUTION_ID> --paths "/*"
```

## Environment URLs

### Development (OneL-DV2)
- **Website**: https://d3j5z1r06hg5fy.cloudfront.net
- **Stack**: `OneL-DV2`

### Production (OneL-Prod)
- **Website**: https://d3kb9da2xipcfv.cloudfront.net
- **Stack**: `OneL-Prod`

## Monitoring and Verification

### Health Checks

```bash
# Check stack status
aws cloudformation describe-stacks --stack-name $STACK_NAME

# Get important outputs
aws cloudformation describe-stacks \
  --stack-name $STACK_NAME \
  --query 'Stacks[0].Outputs'

# Test API connectivity
API_URL=$(aws cloudformation describe-stacks \
  --stack-name $STACK_NAME \
  --query 'Stacks[0].Outputs[?contains(OutputKey, `ApiUrl`)].OutputValue' \
  --output text)
curl $API_URL
```

### CloudWatch Logs

```bash
# View Lambda function logs
aws logs describe-log-groups --log-group-name-prefix "/aws/lambda/$STACK_NAME"

# Tail specific Lambda function logs
aws logs tail /aws/lambda/$STACK_NAME-stepfunctions-startworkflow --follow
```

## Troubleshooting

### Common Deployment Issues

#### 1. **Stack in DELETE_FAILED state**
If a previous deployment failed and the stack is stuck:
```bash
# Delete the stack, skipping failed resources
aws cloudformation delete-stack --stack-name $STACK_NAME --retain-resources <RESOURCE_ID>
```
Or use the AWS Console to delete with "retain resources" option.

#### 2. **Cognito Domain Already Exists**
```bash
# Check if domain is available
aws cognito-idp describe-user-pool-domain --domain $COGNITO_DOMAIN_NAME

# Use different domain name in environment variable
export COGNITO_DOMAIN_NAME="one-l-auth-unique-suffix"
```

#### 3. **Orphaned Resources Blocking Deployment**
If you see `ResourceExistenceCheck` errors, manually delete orphaned resources:
- Lambda functions with the stack prefix
- IAM roles with the stack prefix
- CloudWatch Log Groups
- S3 buckets (empty first, then delete)

#### 4. **CDK Bootstrap Version Mismatch**
```bash
# Re-bootstrap with latest version
cdk bootstrap --force
```

### Debug Commands

```bash
# CDK debug information
cdk doctor

# CloudFormation stack events
aws cloudformation describe-stack-events --stack-name $STACK_NAME

# Lambda function configuration
aws lambda get-function-configuration --function-name $STACK_NAME-stepfunctions-startworkflow
```

## Clean Up

```bash
# Delete entire stack
cdk destroy

# Or via CloudFormation
aws cloudformation delete-stack --stack-name $STACK_NAME
```

**Warning**: This will delete all resources including S3 buckets and DynamoDB tables.

## Security Considerations

### Production Hardening
- Enable AWS CloudTrail for audit logging
- Configure AWS Config for compliance monitoring
- Set up AWS GuardDuty for threat detection
- Implement AWS WAF for web application protection
- Use AWS Secrets Manager for sensitive configuration

### Access Control
- Create dedicated IAM roles for different environments
- Use AWS Organizations for account separation
- Implement least privilege access principles
- Enable MFA for all administrative access

## Support

For additional support, refer to the [CONTRIBUTING.md](../CONTRIBUTING.md) guide or open an issue in the project repository.
