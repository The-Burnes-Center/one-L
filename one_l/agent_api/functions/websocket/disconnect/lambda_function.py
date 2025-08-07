"""
WebSocket disconnect handler Lambda function.
Manages WebSocket connection cleanup.
"""

import json
import boto3
import logging
import os
from datetime import datetime, timezone
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
    Handle WebSocket disconnection requests.
    
    Expected event format from API Gateway WebSocket:
    {
        "requestContext": {
            "connectionId": "abc123",
            "routeKey": "$disconnect",
            "eventType": "DISCONNECT"
        }
    }
    """
    
    try:
        logger.info(f"WebSocket disconnect request: {json.dumps(event, default=str)}")
        
        # Extract connection information
        request_context = event.get('requestContext', {})
        connection_id = request_context.get('connectionId')
        
        # Validate required parameters
        if not connection_id:
            logger.error("No connection ID provided")
            return {'statusCode': 400}
        
        # Remove connection from DynamoDB
        try:
            table = dynamodb.Table(CONNECTIONS_TABLE)
            
            # First, get the connection to log user info
            try:
                response = table.get_item(Key={'connection_id': connection_id})
                if 'Item' in response:
                    user_id = response['Item'].get('user_id', 'unknown')
                    logger.info(f"Disconnecting user {user_id} with connection {connection_id}")
                else:
                    logger.warning(f"Connection {connection_id} not found in database")
            except Exception as e:
                logger.warning(f"Could not retrieve connection info: {e}")
            
            # Delete the connection record
            # We need to get the sort key (user_id) first, then delete
            # For simplicity, we'll scan for the connection_id
            response = table.scan(
                FilterExpression='connection_id = :conn_id',
                ExpressionAttributeValues={':conn_id': connection_id}
            )
            
            for item in response.get('Items', []):
                table.delete_item(
                    Key={
                        'connection_id': item['connection_id'],
                        'user_id': item['user_id']
                    }
                )
                logger.info(f"Removed connection {connection_id} for user {item['user_id']}")
            
        except Exception as e:
            logger.error(f"Failed to remove connection from DynamoDB: {e}")
            # Don't fail the disconnect for this - connection is already closed
        
        logger.info(f"WebSocket disconnection handled successfully: {connection_id}")
        return {'statusCode': 200}
        
    except Exception as e:
        logger.error(f"Error in WebSocket disconnect handler: {str(e)}")
        return {'statusCode': 500}