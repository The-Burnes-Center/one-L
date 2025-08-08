"""
Lambda function for generating frontend config.json with deployment-time values.
This function is part of the user interface configuration system.
"""

import json
import boto3
import os
from typing import Dict, Any
import logging

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Initialize S3 client
s3_client = boto3.client('s3')

def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Lambda function to generate frontend config.json with real deployment values.
    
    Expected Custom Resource invocation format:
    {
        "RequestType": "Create" | "Update" | "Delete",
        "ResourceProperties": {...}
    }
    
    Expected direct invocation format:
    {
        "action": "generate"
    }
    """
    
    try:
        logger.info(f"Generate config request received: {json.dumps(event, default=str)}")
        
        # Handle CloudFormation Custom Resource format
        if 'RequestType' in event:
            return handle_custom_resource(event, context)
        
        # Handle direct invocation
        action = event.get('action', 'generate')
        logger.info(f"Direct invocation - action: {action}")
        
        if action == "generate":
            return generate_config_json()
        else:
            return create_error_response(400, f"Invalid action: {action}")
        
    except Exception as e:
        logger.error(f"Error in generate_config function: {str(e)}")
        return create_error_response(500, f"Internal server error: {str(e)}")


def handle_custom_resource(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """Handle CloudFormation Custom Resource requests."""
    import urllib3
    
    request_type = event['RequestType']
    response_url = event['ResponseURL']
    stack_id = event['StackId']
    request_id = event['RequestId']
    logical_resource_id = event['LogicalResourceId']
    
    response_data = {}
    status = 'SUCCESS'
    reason = ''
    
    try:
        if request_type in ['Create', 'Update']:
            # Generate config.json
            config_result = generate_config_json()
            if config_result['statusCode'] == 200:
                response_data = {'Status': 'Config generated successfully'}
                reason = 'Config.json generated successfully'
            else:
                status = 'FAILED'
                reason = 'Failed to generate config.json'
        elif request_type == 'Delete':
            # No action needed on delete
            reason = 'Delete request - no action needed'
        
    except Exception as e:
        logger.error(f"Custom resource error: {str(e)}")
        status = 'FAILED'
        reason = str(e)
    
    # Send response back to CloudFormation
    response_body = {
        'Status': status,
        'Reason': reason,
        'PhysicalResourceId': logical_resource_id,
        'StackId': stack_id,
        'RequestId': request_id,
        'LogicalResourceId': logical_resource_id,
        'Data': response_data
    }
    
    try:
        http = urllib3.PoolManager()
        response = http.request(
            'PUT',
            response_url,
            body=json.dumps(response_body),
            headers={'Content-Type': 'application/json'}
        )
        logger.info(f"CloudFormation response sent: {response.status}")
    except Exception as e:
        logger.error(f"Failed to send CloudFormation response: {str(e)}")
    
    return {'statusCode': 200, 'body': json.dumps(response_body)}


def generate_config_json() -> Dict[str, Any]:
    """Generate config.json with deployment-time values."""
    try:
        # Get environment variables
        website_bucket = os.environ.get('WEBSITE_BUCKET')
        api_gateway_url = os.environ.get('API_GATEWAY_URL')
        user_pool_id = os.environ.get('USER_POOL_ID')
        user_pool_client_id = os.environ.get('USER_POOL_CLIENT_ID')
        user_pool_domain = os.environ.get('USER_POOL_DOMAIN')
        region = os.environ.get('REGION')
        stack_name = os.environ.get('STACK_NAME')
        websocket_url = os.environ.get('WEBSOCKET_URL')
        
        # Validate required environment variables
        required_vars = {
            'WEBSITE_BUCKET': website_bucket,
            'API_GATEWAY_URL': api_gateway_url,
            'USER_POOL_ID': user_pool_id,
            'USER_POOL_CLIENT_ID': user_pool_client_id,
            'USER_POOL_DOMAIN': user_pool_domain,
            'REGION': region,
            'STACK_NAME': stack_name,
            'WEBSOCKET_URL': websocket_url
        }
        
        missing_vars = [key for key, value in required_vars.items() if not value]
        if missing_vars:
            return create_error_response(500, f"Missing environment variables: {', '.join(missing_vars)}")
        
        # Ensure API Gateway URL ends with /
        if not api_gateway_url.endswith('/'):
            api_gateway_url += '/'
        
        # Create config data matching frontend expectations exactly
        config_data = {
            "apiGatewayUrl": api_gateway_url,
            "userPoolId": user_pool_id,
            "userPoolClientId": user_pool_client_id,
            "userPoolDomain": user_pool_domain,
            "region": region,
            "stackName": stack_name,
            "knowledgeManagementUploadEndpointUrl": f"{api_gateway_url}knowledge_management/upload",
            "knowledgeManagementRetrieveEndpointUrl": f"{api_gateway_url}knowledge_management/retrieve",
            "knowledgeManagementDeleteEndpointUrl": f"{api_gateway_url}knowledge_management/delete",
            "knowledgeManagementSyncEndpointUrl": f"{api_gateway_url}knowledge_management/sync",
            "webSocketUrl": websocket_url
        }
        
        # Write config.json to S3
        s3_client.put_object(
            Bucket=website_bucket,
            Key='config.json',
            Body=json.dumps(config_data, indent=2),
            ContentType='application/json',
            CacheControl='no-cache, no-store, must-revalidate'
        )
        
        logger.info(f"Successfully generated config.json in bucket {website_bucket}")
        logger.info(f"Config data: {json.dumps(config_data, indent=2)}")
        
        return create_success_response({
            "message": "Config.json generated successfully",
            "bucket": website_bucket,
            "config": config_data
        })
        
    except Exception as e:
        logger.error(f"Error generating config.json: {str(e)}")
        return create_error_response(500, f"Error generating config.json: {str(e)}")


def create_success_response(data: Dict[str, Any]) -> Dict[str, Any]:
    """Create successful response."""
    return {
        'statusCode': 200,
        'headers': {
            'Content-Type': 'application/json',
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Methods': 'GET, POST, PUT, DELETE, OPTIONS',
            'Access-Control-Allow-Headers': 'Content-Type, X-Amz-Date, Authorization, X-Api-Key, X-Amz-Security-Token'
        },
        'body': json.dumps(data)
    }


def create_error_response(status_code: int, message: str, data: Dict[str, Any] = None) -> Dict[str, Any]:
    """Create error response."""
    error_body = {'error': message, 'status_code': status_code}
    if data:
        error_body.update(data)
    
    return {
        'statusCode': status_code,
        'headers': {
            'Content-Type': 'application/json',
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Methods': 'GET, POST, PUT, DELETE, OPTIONS',
            'Access-Control-Allow-Headers': 'Content-Type, X-Amz-Date, Authorization, X-Api-Key, X-Amz-Security-Token'
        },
        'body': json.dumps(error_body)
    } 