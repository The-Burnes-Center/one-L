"""
WebSocket connect handler Lambda function.
Manages WebSocket connection establishment and user authentication.
"""

import json
import boto3
import logging
import os
from datetime import datetime, timezone, timedelta
from typing import Dict, Any

# Set up logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Initialize AWS clients
dynamodb = boto3.resource('dynamodb')

# Environment variables
CONNECTIONS_TABLE = os.environ.get('CONNECTIONS_TABLE')


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Handle WebSocket connection requests.
    
    Expected event format from API Gateway WebSocket:
    {
        "requestContext": {
            "connectionId": "abc123",
            "routeKey": "$connect",
            "eventType": "CONNECT",
            "queryStringParameters": {
                "userId": "user123",
                "sessionId": "session456"
            }
        }
    }
    """
    
    try:
        logger.info(f"WebSocket connect request: {json.dumps(event, default=str)}")
        
        # Extract connection information
        request_context = event.get('requestContext', {})
        connection_id = request_context.get('connectionId')
        query_params = event.get('queryStringParameters') or {}
        
        # Validate required parameters
        if not connection_id:
            logger.error("No connection ID provided")
            return {'statusCode': 400}
        
        # Extract user information from query parameters
        user_id = query_params.get('userId')
        session_id = query_params.get('sessionId')
        
        if not user_id:
            logger.error("No user ID provided in query parameters")
            return {'statusCode': 400}
        
        # Store connection in DynamoDB
        connection_data = {
            'connection_id': connection_id,
            'user_id': user_id,
            'session_id': session_id,
            'connected_at': datetime.now(timezone.utc).isoformat(),
            'ttl': int((datetime.now(timezone.utc) + timedelta(hours=24)).timestamp()),  # Auto-expire in 24 hours
            'status': 'connected'
        }
        
        try:
            table = dynamodb.Table(CONNECTIONS_TABLE)
            table.put_item(Item=connection_data)
            logger.info(f"Stored connection {connection_id} for user {user_id}")
        except Exception as e:
            logger.error(f"Failed to store connection in DynamoDB: {e}")
            return {'statusCode': 500}
        
        # Send welcome message
        try:
            apigateway_client = boto3.client('apigatewaymanagementapi',
                endpoint_url=f"https://{request_context.get('apiId')}.execute-api.{os.environ.get('AWS_REGION', 'us-east-1')}.amazonaws.com/{request_context.get('stage', 'prod')}"
            )
            
            welcome_message = {
                'type': 'connection_established',
                'message': 'WebSocket connection established successfully',
                'connectionId': connection_id,
                'timestamp': datetime.now(timezone.utc).isoformat()
            }
            
            apigateway_client.post_to_connection(
                ConnectionId=connection_id,
                Data=json.dumps(welcome_message)
            )
            
            logger.info(f"Sent welcome message to connection {connection_id}")
            
        except Exception as e:
            logger.warning(f"Failed to send welcome message: {e}")
            # Don't fail the connection for this
        
        logger.info(f"WebSocket connection established successfully: {connection_id}")
        return {'statusCode': 200}
        
    except Exception as e:
        logger.error(f"Error in WebSocket connect handler: {str(e)}")
        return {'statusCode': 500}


def get_user_from_token(authorization_header: str) -> str:
    """
    Extract user ID from authorization token.
    This is a simplified implementation - in production, you'd validate the JWT token.
    """
    try:
        # For now, we'll extract user from query parameters
        # In production, you'd validate JWT token here
        return None
    except Exception as e:
        logger.error(f"Failed to extract user from token: {e}")
        return None