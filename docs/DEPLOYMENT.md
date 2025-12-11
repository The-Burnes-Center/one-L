# One-L Deployment Guide

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
STACK_NAME = "YourStackName"  # MUST be set - use your actual stack name

# Cognito domain name (must be globally unique)
COGNITO_DOMAIN_NAME = "your-unique-domain-name"  # MUST be globally unique
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
# Replace <STACK_NAME> with your actual stack name from constants.py
aws cloudformation describe-stacks --stack-name <STACK_NAME>

# Get important outputs
aws cloudformation describe-stacks \
  --stack-name <STACK_NAME> \
  --query 'Stacks[0].Outputs'
```

#### Update Cognito Callback URLs (if needed)
The system automatically updates Cognito callback URLs, but you can manually verify:
```bash
# Get User Pool Client ID from outputs
USER_POOL_CLIENT_ID=$(aws cloudformation describe-stacks \
  --stack-name <STACK_NAME> \
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
  --stack-name <STACK_NAME> \
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
  --stack-name <STACK_NAME> \
  --query 'Stacks[0].Outputs[?OutputKey==`SyncKnowledgeBaseFunctionArn`].OutputValue' \
  --output text)

# Trigger manual sync
# Replace <STACK_NAME> with your actual stack name from constants.py
aws lambda invoke \
  --function-name <STACK_NAME>-sync-knowledge-base \
  --payload '{"action": "start_sync", "data_source": "all"}' \
  response.json
```

## Environment-Specific Deployments

### Development Environment
```bash
# Deploy with development settings
cdk deploy --context environment=dev

# Use separate stack name (update constants.py with your dev stack name)
cdk deploy
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
   # Replace <STACK_NAME> with your actual stack name from constants.py
   API_URL=$(aws cloudformation describe-stacks \
     --stack-name <STACK_NAME> \
     --query 'Stacks[0].Outputs[?OutputKey==`MainApiUrl`].OutputValue' \
     --output text)
   
   # Test API connectivity
   curl $API_URL/knowledge_management/retrieve
   ```

2. **WebSocket API**
   ```bash
   # Get WebSocket URL
   # Replace <STACK_NAME> with your actual stack name from constants.py
   WS_URL=$(aws cloudformation describe-stacks \
     --stack-name <STACK_NAME> \
     --query 'Stacks[0].Outputs[?OutputKey==`WebSocketApiUrl`].OutputValue' \
     --output text)
   
   echo "WebSocket URL: $WS_URL"
   ```

3. **Frontend Application**
   ```bash
   # Get CloudFront URL
   # Replace <STACK_NAME> with your actual stack name from constants.py
   WEBSITE_URL=$(aws cloudformation describe-stacks \
     --stack-name <STACK_NAME> \
     --query 'Stacks[0].Outputs[?OutputKey==`WebsiteUrl`].OutputValue' \
     --output text)
   
   echo "Website URL: $WEBSITE_URL"
   curl -I $WEBSITE_URL
   ```

### CloudWatch Logs
```bash
# View Lambda function logs
# Replace <STACK_NAME> with your actual stack name from constants.py
aws logs describe-log-groups --log-group-name-prefix "/aws/lambda/<STACK_NAME>"

# Tail Step Functions state machine logs
# Replace <STACK_NAME> with your actual stack name from constants.py
aws logs tail /aws/vendedlogs/states/<STACK_NAME>-document-review --follow

# Tail specific Lambda function logs (example: start workflow)
aws logs tail /aws/lambda/<STACK_NAME>-stepfunctions-startworkflow --follow
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
# Replace <STACK_NAME> with your actual stack name from constants.py
aws cloudformation describe-stack-events --stack-name <STACK_NAME>

# Lambda function configuration
# Get Step Functions state machine details
# Replace <STACK_NAME>, <region>, and <account> with actual values
aws stepfunctions describe-state-machine --state-machine-arn "arn:aws:states:<region>:<account>:stateMachine:<STACK_NAME>-document-review"

# Get Lambda function configuration (example: start workflow)
# Replace <STACK_NAME> with your actual stack name from constants.py
aws lambda get-function-configuration --function-name <STACK_NAME>-stepfunctions-startworkflow

# Check IAM role permissions
# Replace <STACK_NAME> with your actual stack name from constants.py
aws iam get-role-policy --role-name <STACK_NAME>-DocumentReviewRole --policy-name policy-name
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
# Replace <STACK_NAME> with your actual stack name from constants.py
aws cloudformation get-template \
  --stack-name <STACK_NAME> \
  --template-stage Processed > backup-template.json

# Backup DynamoDB tables
aws dynamodb create-backup \
  --table-name <STACK_NAME>-analysis-results \
  --backup-name <STACK_NAME>-backup-$(date +%Y%m%d)
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
# Create CloudWatch alarms for Step Functions executions
# Replace <STACK_NAME> with your actual stack name from constants.py
aws cloudwatch put-metric-alarm \
  --alarm-name "<STACK_NAME>-StepFunctions-ExecutionFailures" \
  --alarm-description "Step Functions execution failures" \
  --metric-name ExecutionsFailed \
  --namespace AWS/States \
  --statistic Sum \
  --period 300 \
  --threshold 3 \
  --comparison-operator GreaterThanThreshold \
  --dimensions Name=StateMachineArn,Value="arn:aws:states:<region>:<account>:stateMachine:<STACK_NAME>-document-review"

# Create CloudWatch alarms for Lambda function errors
aws cloudwatch put-metric-alarm \
  --alarm-name "<STACK_NAME>-StepFunctions-Lambda-Errors" \
  --alarm-description "Step Functions Lambda function errors" \
  --metric-name Errors \
  --namespace AWS/Lambda \
  --statistic Sum \
  --period 300 \
  --threshold 5 \
  --comparison-operator GreaterThanThreshold

# Monitor Step Functions execution duration
aws cloudwatch put-metric-alarm \
  --alarm-name "<STACK_NAME>-StepFunctions-LongExecution" \
  --alarm-description "Step Functions executions exceeding 10 minutes" \
  --metric-name ExecutionTime \
  --namespace AWS/States \
  --statistic Average \
  --period 300 \
  --threshold 600 \
  --comparison-operator GreaterThanThreshold
```

### Cost Optimization
- **Step Functions Pricing**: Pay per state transition (first 4,000 free per month)
- **Lambda Optimization**: Right-size memory allocation for Step Functions Lambda functions
- **Monitor AWS costs**: Use Cost Explorer with Step Functions and Lambda filters
- **Set up billing alerts**: Configure budgets for Step Functions and Lambda spend
- **Use AWS Budgets**: Track Step Functions execution costs
- **Review CloudWatch logs retention**: Adjust retention periods for Step Functions execution logs
- **Optimize workflow**: Reduce unnecessary state transitions in Step Functions definition
- **Parallel processing**: Leverage Step Functions Map state for cost-effective concurrent processing

For additional support, refer to the [CONTRIBUTING.md](../CONTRIBUTING.md) guide or open an issue in the project repository.
