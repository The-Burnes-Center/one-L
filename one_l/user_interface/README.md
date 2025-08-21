# One-L Frontend

React frontend for the One-L legal document analysis system.

## Local Development Setup

### Prerequisites
- Node.js 18+ 
- npm

### Quick Start

1. **Install dependencies**
   ```bash
   npm install
   ```

2. **Create environment file**
   
   Create a `.env` file in this directory:
   ```env
   REACT_APP_API_GATEWAY_URL=https://your-api-id.execute-api.region.amazonaws.com/prod
   REACT_APP_USER_POOL_ID=region_userPoolId
   REACT_APP_USER_POOL_CLIENT_ID=userPoolClientId
   REACT_APP_USER_POOL_DOMAIN=https://domain.auth.region.amazoncognito.com
   REACT_APP_WEBSOCKET_URL=wss://websocket-api-id.execute-api.region.amazonaws.com/prod
   REACT_APP_REGION=us-east-1
   REACT_APP_STACK_NAME=OneLStack
   ```

3. **Start development server**
   ```bash
   npm start
   ```

4. **Build for production**
   ```bash
   npm run build
   ```

## Environment Configuration

### Getting Values from CloudFormation Outputs

After deploying the CDK stack, obtain the required values from CloudFormation outputs:

```bash
# Get all stack outputs
aws cloudformation describe-stacks --stack-name OneLStack --query 'Stacks[0].Outputs'

# Or get specific values:
aws cloudformation describe-stacks --stack-name OneLStack \
  --query 'Stacks[0].Outputs[?OutputKey==`MainApiUrl`].OutputValue' --output text

aws cloudformation describe-stacks --stack-name OneLStack \
  --query 'Stacks[0].Outputs[?OutputKey==`UserPoolId`].OutputValue' --output text
```

### CI/CD Usage

For automated deployments, extract environment variables from CloudFormation:

```bash
#!/bin/bash
# Example CI/CD script
export REACT_APP_API_GATEWAY_URL=$(aws cloudformation describe-stacks --stack-name OneLStack --query 'Stacks[0].Outputs[?OutputKey==`MainApiUrl`].OutputValue' --output text)
export REACT_APP_USER_POOL_ID=$(aws cloudformation describe-stacks --stack-name OneLStack --query 'Stacks[0].Outputs[?OutputKey==`UserPoolId`].OutputValue' --output text)
# ... other exports

npm run build
```

## Production Deployment

The app is automatically deployed via CDK to:
- **S3**: Static hosting
- **CloudFront**: Global CDN
- **Auto-config**: Runtime configuration generated automatically 