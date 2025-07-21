# Frontend Configuration Lambda

This directory contains the Lambda function responsible for generating `config.json` with real deployment values post-deployment.

## How It Works

1. **Deploy Time**: CDK deploys all AWS resources (API Gateway, Cognito, etc.)
2. **Post-Deployment**: Custom Resource triggers `generate_config_lambda.py` 
3. **Config Generation**: Lambda writes `config.json` to website S3 bucket with real values
4. **Frontend Load**: React app loads `config.json` with actual deployment URLs and IDs

## Files

- `generate_config_lambda.py` - Lambda function that generates config.json
- This Lambda is part of the UserInterface construct, not the agent API functions

## Environment Variables

The Lambda receives these environment variables from CDK:

- `WEBSITE_BUCKET` - S3 bucket name for website
- `API_GATEWAY_URL` - Real API Gateway URL
- `USER_POOL_ID` - Cognito User Pool ID  
- `USER_POOL_CLIENT_ID` - Cognito User Pool Client ID
- `USER_POOL_DOMAIN` - Cognito hosted UI domain
- `REGION` - AWS region
- `STACK_NAME` - CloudFormation stack name

## Generated Config

The Lambda generates `config.json` with these exact keys (matching frontend expectations):

```json
{
  "apiGatewayUrl": "https://abc123.execute-api.us-east-1.amazonaws.com/prod/",
  "userPoolId": "us-east-1_ABC123XYZ", 
  "userPoolClientId": "abcdef123456789",
  "userPoolDomain": "https://domain.auth.us-east-1.amazoncognito.com",
  "region": "us-east-1",
  "stackName": "OneLStack",
  "knowledgeManagementUploadEndpointUrl": "https://abc123.execute-api.us-east-1.amazonaws.com/prod/knowledge_management/upload",
  "knowledgeManagementRetrieveEndpointUrl": "https://abc123.execute-api.us-east-1.amazonaws.com/prod/knowledge_management/retrieve", 
  "knowledgeManagementDeleteEndpointUrl": "https://abc123.execute-api.us-east-1.amazonaws.com/prod/knowledge_management/delete",
  "knowledgeManagementSyncEndpointUrl": "https://abc123.execute-api.us-east-1.amazonaws.com/prod/knowledge_management/sync"
}
```

## Benefits

✅ **Real Values**: No CDK tokens or placeholders  
✅ **Post-Deployment**: Generated after all AWS resources exist  
✅ **Frontend Integration**: Exact variable names expected by config.js  
✅ **Proper Location**: Part of user interface, not backend functions  
✅ **No Manual Intervention**: Fully automated during deployment 