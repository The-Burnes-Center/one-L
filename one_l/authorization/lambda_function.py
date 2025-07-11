"""
Authentication Lambda function for Cognito user management.
"""

import json
import logging
import os
import boto3
from botocore.exceptions import ClientError

# Configure logging
log_level = os.environ.get('LOG_LEVEL', 'INFO')
logging.basicConfig(level=getattr(logging, log_level))
logger = logging.getLogger(__name__)

# Initialize Cognito client
cognito_client = boto3.client('cognito-idp')
user_pool_id = os.environ['USER_POOL_ID']
user_pool_client_id = os.environ['USER_POOL_CLIENT_ID']


def lambda_handler(event, context):
    """
    Main Lambda handler for authentication operations.
    """
    try:
        logger.info(f"Authentication request: {json.dumps(event, default=str)}")
        
        action = event.get('action', 'authenticate')
        
        if action == 'authenticate':
            return authenticate_user(event)
        elif action == 'refresh':
            return refresh_tokens(event)
        elif action == 'validate':
            return validate_token(event)
        else:
            return create_response(400, {'error': 'Invalid action'})
    
    except Exception as e:
        logger.error(f"Error: {str(e)}")
        return create_response(500, {'error': 'Internal server error'})


def authenticate_user(event):
    """Authenticate user with username and password."""
    try:
        username = event.get('username')
        password = event.get('password')
        
        if not username or not password:
            return create_response(400, {'error': 'Username and password required'})
        
        response = cognito_client.admin_initiate_auth(
            UserPoolId=user_pool_id,
            ClientId=user_pool_client_id,
            AuthFlow='ADMIN_NO_SRP_AUTH',
            AuthParameters={
                'USERNAME': username,
                'PASSWORD': password
            }
        )
        
        return create_response(200, {
            'access_token': response['AuthenticationResult']['AccessToken'],
            'id_token': response['AuthenticationResult']['IdToken'],
            'refresh_token': response['AuthenticationResult']['RefreshToken'],
            'expires_in': response['AuthenticationResult']['ExpiresIn']
        })
        
    except ClientError as e:
        logger.error(f"Authentication failed: {str(e)}")
        return create_response(401, {'error': 'Authentication failed'})


def refresh_tokens(event):
    """Refresh authentication tokens."""
    try:
        refresh_token = event.get('refresh_token')
        
        if not refresh_token:
            return create_response(400, {'error': 'Refresh token required'})
        
        response = cognito_client.admin_initiate_auth(
            UserPoolId=user_pool_id,
            ClientId=user_pool_client_id,
            AuthFlow='REFRESH_TOKEN_AUTH',
            AuthParameters={
                'REFRESH_TOKEN': refresh_token
            }
        )
        
        return create_response(200, {
            'access_token': response['AuthenticationResult']['AccessToken'],
            'id_token': response['AuthenticationResult']['IdToken'],
            'expires_in': response['AuthenticationResult']['ExpiresIn']
        })
        
    except ClientError as e:
        logger.error(f"Token refresh failed: {str(e)}")
        return create_response(401, {'error': 'Token refresh failed'})


def validate_token(event):
    """Validate an access token."""
    try:
        access_token = event.get('access_token')
        
        if not access_token:
            return create_response(400, {'error': 'Access token required'})
        
        response = cognito_client.get_user(AccessToken=access_token)
        
        attributes = {}
        for attr in response['UserAttributes']:
            attributes[attr['Name']] = attr['Value']
        
        return create_response(200, {
            'valid': True,
            'username': response['Username'],
            'attributes': attributes
        })
        
    except ClientError as e:
        logger.error(f"Token validation failed: {str(e)}")
        return create_response(401, {'error': 'Token validation failed'})


def create_response(status_code, body):
    """Create a standardized response."""
    return {
        'statusCode': status_code,
        'headers': {
            'Content-Type': 'application/json',
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Headers': 'Content-Type,Authorization',
            'Access-Control-Allow-Methods': 'POST,OPTIONS'
        },
        'body': json.dumps(body)
    } 