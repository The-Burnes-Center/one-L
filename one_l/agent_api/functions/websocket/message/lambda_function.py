"""
WebSocket message handler Lambda function.
Handles incoming WebSocket messages from clients.
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
    Handle WebSocket messages from clients.
    
    Expected event format from API Gateway WebSocket:
    {
        "requestContext": {
            "connectionId": "abc123",
            "routeKey": "$default"
        },
        "body": "{\"action\": \"subscribe\", \"jobId\": \"job123\"}"
    }
    """
    
    try:
        logger.info(f"WebSocket message received: {json.dumps(event, default=str)}")
        
        # Extract connection information
        request_context = event.get('requestContext', {})
        connection_id = request_context.get('connectionId')
        
        # Validate connection ID
        if not connection_id:
            logger.error("No connection ID provided")
            return {'statusCode': 400}
        
        # Parse message body
        try:
            message_body = json.loads(event.get('body', '{}'))
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON in message body: {e}")
            return send_error_message(request_context, connection_id, "Invalid JSON format")
        
        # Extract action from message
        action = message_body.get('action')
        
        if not action:
            logger.error("No action specified in message")
            return send_error_message(request_context, connection_id, "No action specified")
        
        # Handle different message actions
        if action == 'subscribe':
            return handle_subscribe(request_context, connection_id, message_body)
        elif action == 'unsubscribe':
            return handle_unsubscribe(request_context, connection_id, message_body)
        elif action == 'ping':
            return handle_ping(request_context, connection_id, message_body)
        else:
            logger.warning(f"Unknown action: {action}")
            return send_error_message(request_context, connection_id, f"Unknown action: {action}")
        
    except Exception as e:
        logger.error(f"Error in WebSocket message handler: {str(e)}")
        return {'statusCode': 500}


def handle_subscribe(request_context: Dict, connection_id: str, message_body: Dict) -> Dict[str, Any]:
    """Handle subscription to job updates or session updates."""
    try:
        job_id = message_body.get('jobId')
        session_id = message_body.get('sessionId')
        subscribe_to_session = message_body.get('subscribeToSession', False)
        
        # Allow either job_id or session-level subscription
        if not job_id and not (session_id and subscribe_to_session):
            return send_error_message(request_context, connection_id, "Either jobId or sessionId with subscribeToSession=true is required")
        
        # Update connection record with subscription info
        table = dynamodb.Table(CONNECTIONS_TABLE)
        
        # Get current connection data
        response = table.scan(
            FilterExpression='connection_id = :conn_id',
            ExpressionAttributeValues={':conn_id': connection_id}
        )
        
        if not response.get('Items'):
            return send_error_message(request_context, connection_id, "Connection not found")
        
        connection_item = response['Items'][0]
        
        # Update with subscription info
        update_expression_parts = ['updated_at = :updated_at']
        expression_values = {':updated_at': datetime.now(timezone.utc).isoformat()}
        
        if job_id:
            update_expression_parts.append('subscribed_job_id = :job_id')
            expression_values[':job_id'] = job_id
        
        if session_id:
            update_expression_parts.append('subscribed_session_id = :session_id')
            expression_values[':session_id'] = session_id
            
        if subscribe_to_session:
            update_expression_parts.append('subscribed_to_session = :subscribe_session')
            expression_values[':subscribe_session'] = True
        
        table.update_item(
            Key={
                'connection_id': connection_id,
                'user_id': connection_item['user_id']
            },
            UpdateExpression='SET ' + ', '.join(update_expression_parts),
            ExpressionAttributeValues=expression_values
        )
        
        # Send confirmation
        response_message = {
            'type': 'subscription_confirmed',
            'jobId': job_id,
            'sessionId': session_id,
            'message': f'Subscribed to job updates for {job_id}',
            'timestamp': datetime.now(timezone.utc).isoformat()
        }
        
        send_message_to_connection(request_context, connection_id, response_message)
        
        logger.info(f"Connection {connection_id} subscribed to job {job_id}")
        return {'statusCode': 200}
        
    except Exception as e:
        logger.error(f"Error handling subscribe: {e}")
        return send_error_message(request_context, connection_id, f"Subscription failed: {str(e)}")


def handle_unsubscribe(request_context: Dict, connection_id: str, message_body: Dict) -> Dict[str, Any]:
    """Handle unsubscription from job updates."""
    try:
        # Remove subscription info from connection record
        table = dynamodb.Table(CONNECTIONS_TABLE)
        
        # Get current connection data
        response = table.scan(
            FilterExpression='connection_id = :conn_id',
            ExpressionAttributeValues={':conn_id': connection_id}
        )
        
        if not response.get('Items'):
            return send_error_message(request_context, connection_id, "Connection not found")
        
        connection_item = response['Items'][0]
        
        # Remove subscription info
        table.update_item(
            Key={
                'connection_id': connection_id,
                'user_id': connection_item['user_id']
            },
            UpdateExpression='REMOVE subscribed_job_id, subscribed_session_id SET updated_at = :updated_at',
            ExpressionAttributeValues={
                ':updated_at': datetime.now(timezone.utc).isoformat()
            }
        )
        
        # Send confirmation
        response_message = {
            'type': 'unsubscription_confirmed',
            'message': 'Unsubscribed from job updates',
            'timestamp': datetime.now(timezone.utc).isoformat()
        }
        
        send_message_to_connection(request_context, connection_id, response_message)
        
        logger.info(f"Connection {connection_id} unsubscribed from job updates")
        return {'statusCode': 200}
        
    except Exception as e:
        logger.error(f"Error handling unsubscribe: {e}")
        return send_error_message(request_context, connection_id, f"Unsubscription failed: {str(e)}")


def handle_ping(request_context: Dict, connection_id: str, message_body: Dict) -> Dict[str, Any]:
    """Handle ping message for connection keep-alive."""
    try:
        response_message = {
            'type': 'pong',
            'message': 'Connection is alive',
            'timestamp': datetime.now(timezone.utc).isoformat()
        }
        
        send_message_to_connection(request_context, connection_id, response_message)
        
        logger.debug(f"Sent pong to connection {connection_id}")
        return {'statusCode': 200}
        
    except Exception as e:
        logger.error(f"Error handling ping: {e}")
        return {'statusCode': 500}


def send_message_to_connection(request_context: Dict, connection_id: str, message: Dict) -> None:
    """Send a message to a specific WebSocket connection."""
    try:
        apigateway_client = boto3.client('apigatewaymanagementapi',
            endpoint_url=f"https://{request_context.get('apiId')}.execute-api.{os.environ.get('AWS_REGION', 'us-east-1')}.amazonaws.com/{request_context.get('stage', 'prod')}"
        )
        
        apigateway_client.post_to_connection(
            ConnectionId=connection_id,
            Data=json.dumps(message)
        )
        
    except Exception as e:
        logger.error(f"Failed to send message to connection {connection_id}: {e}")
        raise


def send_error_message(request_context: Dict, connection_id: str, error_message: str) -> Dict[str, Any]:
    """Send an error message to a WebSocket connection."""
    try:
        error_response = {
            'type': 'error',
            'message': error_message,
            'timestamp': datetime.now(timezone.utc).isoformat()
        }
        
        send_message_to_connection(request_context, connection_id, error_response)
        
        return {'statusCode': 400}
        
    except Exception as e:
        logger.error(f"Failed to send error message: {e}")
        return {'statusCode': 500}