# One-L Deployment Guide

## Description

Instructions for deploying One L into your AWS environment. The project is a CDK project. The project folder contains:

- The wrapper code that defines AWS resources (Lambda functions, Step Functions state machine, API Gateways, S3 Buckets, DynamoDB tables, etc.) in Python CDK constructs under `one_l/`

- The implementation code for all backend features:
  - **Step Functions workflow Lambda functions** (under `one_l/agent_api/functions/stepfunctions/`) — Python 3.12 scripts that execute the 11-stage document analysis pipeline
  - **Knowledge management Lambda functions** (under `one_l/agent_api/functions/knowledge_management/`) — Python scripts for S3 operations, knowledge base synchronization, and session management
  - **WebSocket Lambda functions** (under `one_l/agent_api/functions/websocket/`) — Python scripts for real-time communication
  - **Agent logic** (under `one_l/agent_api/agent/`) — Python modules for AI-powered conflict detection using Claude Sonnet 4

- The implementation code for the frontend UI (under `one_l/user_interface/`) — A React project with Cloudscape UI components

A CDK project consists of CDK Stacks. The main stack (`OneLStack` in `one_l/one_l_stack.py`) groups related resources. CDK constructs define each component and synthesize into your AWS environment. These can be edited to change function timeouts, memory allocation, or permissions. 

**Note**: Most attributes of a Cognito User Pool cannot be changed after initial deployment. Some changes will cause future deployments to fail, so review AWS documentation to understand what should or should not be changed.

## Overview

One-L uses a **single production stack** (`OneL-Prod`) deployed from the `main` branch. Deployments are automated via **GitHub Actions** - pushing to the `main` branch triggers the production deployment.

## Initial Setup

- Ensure you have a designated AWS account on which to host One L
- Make sure your account allows creation of **3 S3 buckets** (`knowledge-source`, `user-documents`, `agent-processing`) and **3 DynamoDB tables** (`analysis-results`, `sessions`, `websocket-connections`). Accounts have soft limits that may require service requests for higher limits
- Ensure your account is eligible for accessing all services listed in the Architecture Diagram. Some resources may require service requests:
  - **AWS Bedrock** (model access)
  - **Amazon OpenSearch Serverless** (may require service request)
  - **AWS Step Functions** (standard service limits apply)
- Go to Bedrock from the AWS console and enable access to at least the following models (you will only be charged for usage):
  - **Anthropic > Claude Sonnet 4** (`us.anthropic.claude-sonnet-4-20250514-v1:0`)
  - **Amazon > Titan Embed Text v2** (`amazon.titan-embed-text-v2:0`)

## Deployment

1. **Install prerequisites**: VS Code (or preferred IDE), AWS CLI, AWS CDK v2 (`npm install -g aws-cdk`), Docker Desktop, Git, Python 3.12+, Node.js 18+, npm

2. **Create a git repository, connect to VS Code, and clone the repository**

3. **IF USING CODESPACES**: Connect to Codespaces, add AWS credentials to Codespaces, and create a new Codespace using the Git repository

4. **In the `constants.py` file, change `STACK_NAME` and `COGNITO_DOMAIN_NAME` to unique values for your project (case-sensitive)**:
   ```python
   STACK_NAME = "OneL-Prod"  # Change to your unique stack name
   COGNITO_DOMAIN_NAME = "one-l-auth-prod"  # Must be globally unique
   ```
   
   **Also update `.github/workflows/deploy-production.yml`**: If using GitHub Actions for automated deployment, update all instances of `STACK_NAME: "OneL-Prod"` to match your stack name.

5. **Run in terminal**: `aws configure` and add your access key and secret key from AWS Security Credentials > Access Keys. If you don't have one, create a new one and accept default settings. Region: `us-east-1`, Output: `json`
   
   If you have multiple AWS accounts, ensure you are running deployment in the proper one. To view AWS profiles, run: `cat ~/.aws/credentials`
   
   To configure a profile (for multiple credentials): `aws configure --profile my_profile`
   
   To run deployment on a specific account: `cdk deploy --profile my_profile`
   
   If this doesn't work, ensure hard set to specific profile: `export AWS_PROFILE=my_profile`

6. **Create and activate Python virtual environment**:
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate  # Linux/macOS
   # .venv\Scripts\activate  # Windows
   ```

7. **Run `pip install -r requirements.txt`** to install Python dependencies

8. **Ensure Docker Desktop is running** (required for Lambda function bundling during deployment)

9. **Navigate to the frontend directory and install dependencies**:
   ```bash
   cd one_l/user_interface
   npm install
   ```

10. **Run `cdk bootstrap`** (if you haven't already — one-time setup per AWS account/region)

11. **Run `cdk deploy`** (or `cdk deploy OneL-Prod` if you specified a different name in step 4)

## Output

The last command will take approximately **20-30 minutes** to execute. When it completes, you will receive several CloudFormation outputs. Note these, as they will be important later:

- **User Pool ID** (Cognito User Pool identifier)
- **User Pool Client ID** (Cognito App Client identifier)
- **User Pool Domain URL** (Cognito authentication domain)
- **Main API URL** (REST API Gateway endpoint)
- **WebSocket API URL** (WebSocket API endpoint for real-time updates)
- **CloudFront Domain Name** (this is how people will access the application)
- **Website URL** (full CloudFront URL for the React frontend)

## Configuration

### Configuring email for user access and setup

Navigate to the user pool from Cognito in the AWS console and go to the **Messaging** tab. Scroll to the bottom and you should see the message templates. Select **Invitation message** and click **Edit**. Update the email subject as you wish and add the following as the email message:

```html
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>One L Legal Document Review Tool</title>
</head>
<body>
    <p>Hello,</p>
    <p>I am pleased to inform you that the new custom deployment link for One L is now ready for testing. All future updates and changes will be applied to this new link. Please note that the tool is still under development, so you may encounter errors. We kindly request that you record any feedback regarding the tool's performance. Below, you will find the necessary information for signing into the tool.</p>
    <p>When signing in for the first time, please use this link: <a href="https://xxxx-auth.auth.us-east-1.amazoncognito.com/login?client_id=xxxxxxx&response_type=code&scope=aws.cognito.signin.user.admin+email+openid+phone+profile&redirect_uri=https://xxxxxxxxxx.cloudfront.net">First Time Sign-In Link</a></p>
    <p>Once you are registered, you can use the regular custom deployment link: <a href="https://xxxxxxxxxx.cloudfront.net">Regular Custom Deployment Link</a></p>
    <p>Username: {username}</p>
    <p>Temporary Password: {####}</p>
</body>
</html>
```

The `xxxxx` placeholders should be updated accordingly based on your CloudFormation outputs from step 11. If the links do not work when testing, check if the quotes are straight like `"` and not `"`.

### Adding Redirect URL

Navigate to **App Integration** in User Pools and scroll to **App clients and analytics**. Select the app client name; this will open a new page. Scroll to **Hosted UI** and click **Edit**. In the **Allowed callback URLs** and **Sign-out URL**, add:

- `https://xxxxxxxxxx.cloudfront.net` — this will be your deployment URL from step 11

### Adding a new User

Add a user in the User Pool and use the following options to create the user:

- **Send an email invitation**
- Enter user email and enable **"Mark email address as verified"**
- Select **Generate a password**

The user will receive an email with username and password, along with the first time login URL.

To give admin role to a user, navigate to the user → **Edit attributes** → **Optional attributes** → **Attribute name** → `custom:role` → **Value** → `["User","Admin"]`

### Setting up Auto deployment from Github Actions

GitHub Actions workflow is already configured in the repository. Automated deployment is set up in `.github/workflows/deploy.yml`. When you push code to the `main` branch, the workflow will automatically deploy the stack. Ensure that `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` are configured as secrets in your GitHub repository settings.

## Monitoring and Verification

### Health Checks

```bash
# Check stack status
aws cloudformation describe-stacks --stack-name OneL-Prod

# Get important outputs
aws cloudformation describe-stacks \
  --stack-name OneL-Prod \
  --query 'Stacks[0].Outputs'

# Test API connectivity
API_URL=$(aws cloudformation describe-stacks \
  --stack-name OneL-Prod \
  --query 'Stacks[0].Outputs[?contains(OutputKey, `ApiUrl`)].OutputValue' \
  --output text)
curl $API_URL
```

### CloudWatch Logs

```bash
# View Lambda function logs
aws logs describe-log-groups --log-group-name-prefix "/aws/lambda/OneL-Prod"

# Tail specific Lambda function logs
aws logs tail /aws/lambda/OneL-Prod-stepfunctions-startworkflow --follow
```

## Troubleshooting

### Common Deployment Issues

#### 1. **Stack in DELETE_FAILED state**
If a previous deployment failed and the stack is stuck:
```bash
# Delete the stack, skipping failed resources
aws cloudformation delete-stack --stack-name OneL-Prod --retain-resources <RESOURCE_ID>
```
Or use the AWS Console to delete with "retain resources" option.

#### 2. **Cognito Domain Already Exists**
```bash
# Check if domain is available
aws cognito-idp describe-user-pool-domain --domain one-l-auth-prod

# Use different domain name in constants.py
# Update COGNITO_DOMAIN_NAME in constants.py to a unique value
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
aws cloudformation describe-stack-events --stack-name OneL-Prod

# Lambda function configuration
aws lambda get-function-configuration --function-name OneL-Prod-stepfunctions-startworkflow
```

## Clean Up

```bash
# Delete entire stack
cdk destroy OneL-Prod

# Or via CloudFormation
aws cloudformation delete-stack --stack-name OneL-Prod
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
