# One-L Deployment Guide

## Prerequisites

### AWS Account Setup
- **AWS Account** with sufficient permissions for:
  - Lambda, API Gateway, S3, DynamoDB, Cognito
  - Bedrock (Claude 4 Sonnet access)
  - OpenSearch Serverless, CloudFront
- **AWS CLI** configured with appropriate credentials
- **Bedrock Model Access**: Ensure Claude 4 Sonnet is available in your region

### Local Development Environment
- **Python 3.9+** with pip
- **Node.js 18+** with npm
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

## Deployment Steps

### 1. **Clone and Setup**

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

### 2. **Configure Environment**

#### Edit Configuration Constants
```bash
# Edit constants.py to customize deployment
vim constants.py
```

Key configuration options:
```python
# Stack name for all AWS resources
STACK_NAME = "OneLStack"  # Modify as needed

# Cognito domain name (must be globally unique)
COGNITO_DOMAIN_NAME = "one-l-auth"  # Change to unique name
```

#### Verify AWS Configuration
```bash
# Check AWS credentials
aws sts get-caller-identity

# Verify CDK installation
cdk --version

# Check available regions and Bedrock access
aws bedrock list-foundation-models --region us-east-1
```

### 3. **CDK Bootstrap (One-time Setup)**

```bash
# Bootstrap CDK in your AWS account/region
cdk bootstrap

# If deploying to specific account/region
cdk bootstrap aws://ACCOUNT-NUMBER/REGION
```

### 4. **Deploy Infrastructure**

#### Preview Deployment
```bash
# See what will be created
cdk diff

# Generate CloudFormation template
cdk synth
```

#### Deploy Stack
```bash
# Deploy with progress output
cdk deploy --require-approval never

# Deploy with specific profile
cdk deploy --profile your-aws-profile

# Deploy with parameters
cdk deploy --parameters stackName=YourCustomStack
```

**Deployment Time**: Approximately 15-20 minutes for initial deployment.

### 5. **Frontend Build and Deployment**

The frontend is automatically built and deployed as part of the CDK stack:

```bash
# Frontend dependencies are installed automatically
# Build process is handled by CDK during deployment
# CloudFront distribution is created and configured
```

Manual frontend development:
```bash
cd one_l/user_interface
npm install
npm start  # For local development
npm run build  # For production build
```

### 6. **Post-Deployment Configuration**

#### Verify Deployment
```bash
# Check stack status
aws cloudformation describe-stacks --stack-name OneLStack

# Get important outputs
aws cloudformation describe-stacks \
  --stack-name OneLStack \
  --query 'Stacks[0].Outputs'
```

#### Update Cognito Callback URLs (if needed)
The system automatically updates Cognito callback URLs, but you can manually verify:
```bash
# Get User Pool Client ID from outputs
USER_POOL_CLIENT_ID=$(aws cloudformation describe-stacks \
  --stack-name OneLStack \
  --query 'Stacks[0].Outputs[?OutputKey==`UserPoolClientId`].OutputValue' \
  --output text)

# Check callback URLs
aws cognito-idp describe-user-pool-client \
  --user-pool-id <USER_POOL_ID> \
  --client-id $USER_POOL_CLIENT_ID
```

### 7. **Knowledge Base Setup**

#### Upload Reference Documents
```bash
# Upload Massachusetts legal documents to knowledge bucket
KNOWLEDGE_BUCKET=$(aws cloudformation describe-stacks \
  --stack-name OneLStack \
  --query 'Stacks[0].Outputs[?OutputKey==`KnowledgeBucketName`].OutputValue' \
  --output text)

# Upload reference documents
aws s3 cp ./reference-docs/ s3://$KNOWLEDGE_BUCKET/admin-uploads/ --recursive
```

#### Trigger Knowledge Base Sync
The system automatically syncs when files are uploaded, or trigger manually:
```bash
# Get sync function name
SYNC_FUNCTION=$(aws cloudformation describe-stacks \
  --stack-name OneLStack \
  --query 'Stacks[0].Outputs[?OutputKey==`SyncKnowledgeBaseFunctionArn`].OutputValue' \
  --output text)

# Trigger manual sync
aws lambda invoke \
  --function-name OneLStack-sync-knowledge-base \
  --payload '{"action": "start_sync", "data_source": "all"}' \
  response.json
```

## Environment-Specific Deployments

### Development Environment
```bash
# Deploy with development settings
cdk deploy --context environment=dev

# Use separate stack name
cdk deploy OneLStackDev
```

### Production Environment
```bash
# Deploy with production optimizations
cdk deploy --context environment=prod

# Enable additional security features
cdk deploy --context enableWaf=true
```

### Multi-Region Deployment
```bash
# Deploy to different regions
cdk deploy --context region=us-west-2
cdk deploy --context region=eu-west-1
```

## Monitoring and Verification

### Health Checks

1. **API Gateway**
   ```bash
   # Get API Gateway URL
   API_URL=$(aws cloudformation describe-stacks \
     --stack-name OneLStack \
     --query 'Stacks[0].Outputs[?OutputKey==`MainApiUrl`].OutputValue' \
     --output text)
   
   # Test API connectivity
   curl $API_URL/knowledge_management/retrieve
   ```

2. **WebSocket API**
   ```bash
   # Get WebSocket URL
   WS_URL=$(aws cloudformation describe-stacks \
     --stack-name OneLStack \
     --query 'Stacks[0].Outputs[?OutputKey==`WebSocketApiUrl`].OutputValue' \
     --output text)
   
   echo "WebSocket URL: $WS_URL"
   ```

3. **Frontend Application**
   ```bash
   # Get CloudFront URL
   WEBSITE_URL=$(aws cloudformation describe-stacks \
     --stack-name OneLStack \
     --query 'Stacks[0].Outputs[?OutputKey==`WebsiteUrl`].OutputValue' \
     --output text)
   
   echo "Website URL: $WEBSITE_URL"
   curl -I $WEBSITE_URL
   ```

### CloudWatch Logs
```bash
# View Lambda function logs
aws logs describe-log-groups --log-group-name-prefix "/aws/lambda/OneLStack"

# Tail specific function logs
aws logs tail /aws/lambda/OneLStack-document-review --follow
```

## Troubleshooting

### Common Deployment Issues

#### 1. **Bedrock Access Denied**
```bash
# Check if Claude 4 Sonnet is available in your region
aws bedrock list-foundation-models \
  --region us-east-1 \
  --query 'modelSummaries[?contains(modelId, `claude-4`)]'

# Request model access in AWS Console if needed
```

#### 2. **OpenSearch Serverless Limits**
```bash
# Check OpenSearch Serverless quotas
aws opensearchserverless list-collections

# Delete unused collections if at limit
aws opensearchserverless delete-collection --id collection-id
```

#### 3. **Cognito Domain Already Exists**
```bash
# Check if domain is available
aws cognito-idp describe-user-pool-domain --domain one-l-auth

# Use different domain name in constants.py
```

#### 4. **S3 Bucket Name Conflicts**
```bash
# Bucket names must be globally unique
# Modify bucket names in storage construct or use random suffix
```

### Debug Commands

```bash
# CDK debug information
cdk doctor

# CloudFormation stack events
aws cloudformation describe-stack-events --stack-name OneLStack

# Lambda function configuration
aws lambda get-function-configuration --function-name OneLStack-document-review

# Check IAM role permissions
aws iam get-role-policy --role-name OneLStack-DocumentReviewRole --policy-name policy-name
```

## Updating and Maintenance

### Update Deployment
```bash
# Pull latest changes
git pull origin main

# Update dependencies
pip install -r requirements.txt

# Deploy updates
cdk diff  # Preview changes
cdk deploy  # Apply changes
```

### Backup and Recovery
```bash
# Export CloudFormation template
aws cloudformation get-template \
  --stack-name OneLStack \
  --template-stage Processed > backup-template.json

# Backup DynamoDB tables
aws dynamodb create-backup \
  --table-name OneLStack-analysis-results \
  --backup-name OneLStack-backup-$(date +%Y%m%d)
```

### Clean Up
```bash
# Delete entire stack
cdk destroy

# Remove CDK bootstrap (if no other CDK apps)
# Note: This will affect other CDK deployments
aws cloudformation delete-stack --stack-name CDKToolkit
```

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

### Data Protection
- Enable S3 bucket encryption with KMS keys
- Configure DynamoDB encryption at rest
- Use VPC endpoints for internal traffic
- Implement data retention policies

## Support and Maintenance

### Monitoring Setup
```bash
# Create CloudWatch alarms for critical functions
aws cloudwatch put-metric-alarm \
  --alarm-name "OneLStack-DocumentReview-Errors" \
  --alarm-description "Lambda function errors" \
  --metric-name Errors \
  --namespace AWS/Lambda \
  --statistic Sum \
  --period 300 \
  --threshold 5 \
  --comparison-operator GreaterThanThreshold
```

### Cost Optimization
- Monitor AWS costs with Cost Explorer
- Set up billing alerts
- Use AWS Budgets for spend management
- Review CloudWatch logs retention periods
- Optimize Lambda memory allocation based on usage

For additional support, refer to the [CONTRIBUTING.md](../CONTRIBUTING.md) guide or open an issue in the project repository.
