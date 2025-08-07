"""
WebSocket notification handler Lambda function.
Sends real-time notifications to connected WebSocket clients.
This function can be invoked by other services to broadcast updates.
"""

import json
import boto3
import logging
import os
from datetime import datetime, timezone
from typing import Dict, Any, List
from botocore.exceptions import ClientError

# Set up logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Initialize AWS clients
dynamodb = boto3.resource('dynamodb')

# Environment variables
CONNECTIONS_TABLE = os.environ.get('CONNECTIONS_TABLE')
WEBSOCKET_API_ENDPOINT = os.environ.get('WEBSOCKET_API_ENDPOINT')


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Send notifications to WebSocket clients.
    
    Can be invoked in multiple ways:
    1. Direct Lambda invocation from other services
    2. HTTP POST request via API Gateway (REST fallback)
    3. DynamoDB stream trigger (future enhancement)
    
    Expected event format for direct invocation:
    {
        "notification_type": "job_progress",
        "job_id": "job123",
        "user_id": "user456", 
        "session_id": "session789",
        "data": {
            "status": "processing",
            "progress": 50,
            "message": "Analyzing document..."
        }
    }
    
    Expected event format for API Gateway:
    {
        "httpMethod": "POST",
        "body": "{...same as above...}"
    }
    """
    
    try:
        logger.info(f"Notification request received: {json.dumps(event, default=str)}")
        
        # Parse event based on invocation method
        if event.get('httpMethod') == 'POST':
            # Called via API Gateway REST endpoint
            try:
                notification_data = json.loads(event.get('body', '{}'))
            except json.JSONDecodeError as e:
                logger.error(f"Invalid JSON in request body: {e}")
                return create_http_response(400, {'error': 'Invalid JSON format'})
        else:
            # Direct Lambda invocation
            notification_data = event
        
        # Validate required fields
        notification_type = notification_data.get('notification_type')
        if not notification_type:
            error_msg = "notification_type is required"
            logger.error(error_msg)
            return create_response(False, error_msg)
        
        # Route to appropriate handler based on notification type
        if notification_type == 'job_progress':
            return handle_job_progress_notification(notification_data)
        elif notification_type == 'job_completed':
            return handle_job_completed_notification(notification_data)
        elif notification_type == 'session_update':
            return handle_session_update_notification(notification_data)
        elif notification_type == 'broadcast':
            return handle_broadcast_notification(notification_data)
        else:
            error_msg = f"Unknown notification type: {notification_type}"
            logger.error(error_msg)
            return create_response(False, error_msg)
        
    except Exception as e:
        logger.error(f"Error in notification handler: {str(e)}")
        return create_response(False, f"Internal error: {str(e)}")


def handle_job_progress_notification(notification_data: Dict) -> Dict[str, Any]:
    """Handle job progress notifications."""
    try:
        job_id = notification_data.get('job_id')
        user_id = notification_data.get('user_id')
        session_id = notification_data.get('session_id')
        progress_data = notification_data.get('data', {})
        
        if not job_id:
            return create_response(False, "job_id is required for job progress notifications")
        
        # Find connections subscribed to this job
        connections = find_connections_for_job(job_id, user_id)
        
        if not connections:
            logger.info(f"No active connections found for job {job_id}")
            return create_response(True, "No active connections found", sent_count=0)
        
        # Create notification message
        message = {
            'type': 'job_progress',
            'job_id': job_id,
            'session_id': session_id,
            'data': progress_data,
            'timestamp': datetime.now(timezone.utc).isoformat()
        }
        
        # Send to all relevant connections
        sent_count = send_to_connections(connections, message)
        
        logger.info(f"Sent job progress notification for {job_id} to {sent_count} connections")
        return create_response(True, "Job progress notification sent", sent_count=sent_count)
        
    except Exception as e:
        logger.error(f"Error handling job progress notification: {e}")
        return create_response(False, f"Failed to send job progress notification: {str(e)}")


def handle_job_completed_notification(notification_data: Dict) -> Dict[str, Any]:
    """Handle job completion notifications."""
    try:
        job_id = notification_data.get('job_id')
        user_id = notification_data.get('user_id')
        session_id = notification_data.get('session_id')
        completion_data = notification_data.get('data', {})
        
        if not job_id:
            return create_response(False, "job_id is required for job completion notifications")
        
        # Find connections subscribed to this job
        connections = find_connections_for_job(job_id, user_id)
        
        # Also find connections subscribed to the session (for cases where API Gateway timed out)
        if session_id:
            session_connections = find_connections_for_session(session_id, user_id)
            # Merge connections and remove duplicates
            connection_ids = {conn['connection_id'] for conn in connections}
            for conn in session_connections:
                if conn['connection_id'] not in connection_ids:
                    connections.append(conn)
        
        if not connections:
            logger.info(f"No active connections found for job {job_id} or session {session_id}")
            return create_response(True, "No active connections found", sent_count=0)
        
        # Create notification message
        message = {
            'type': 'job_completed',
            'job_id': job_id,
            'session_id': session_id,
            'data': completion_data,
            'timestamp': datetime.now(timezone.utc).isoformat()
        }
        
        # Send to all relevant connections
        sent_count = send_to_connections(connections, message)
        
        logger.info(f"Sent job completion notification for {job_id} to {sent_count} connections")
        return create_response(True, "Job completion notification sent", sent_count=sent_count)
        
    except Exception as e:
        logger.error(f"Error handling job completion notification: {e}")
        return create_response(False, f"Failed to send job completion notification: {str(e)}")


def handle_session_update_notification(notification_data: Dict) -> Dict[str, Any]:
    """Handle session update notifications."""
    try:
        user_id = notification_data.get('user_id')
        session_id = notification_data.get('session_id')
        update_data = notification_data.get('data', {})
        
        if not user_id:
            return create_response(False, "user_id is required for session update notifications")
        
        # Find connections for this user
        connections = find_connections_for_user(user_id)
        
        if not connections:
            logger.info(f"No active connections found for user {user_id}")
            return create_response(True, "No active connections found", sent_count=0)
        
        # Create notification message
        message = {
            'type': 'session_update',
            'user_id': user_id,
            'session_id': session_id,
            'data': update_data,
            'timestamp': datetime.now(timezone.utc).isoformat()
        }
        
        # Send to all relevant connections
        sent_count = send_to_connections(connections, message)
        
        logger.info(f"Sent session update notification for user {user_id} to {sent_count} connections")
        return create_response(True, "Session update notification sent", sent_count=sent_count)
        
    except Exception as e:
        logger.error(f"Error handling session update notification: {e}")
        return create_response(False, f"Failed to send session update notification: {str(e)}")


def handle_broadcast_notification(notification_data: Dict) -> Dict[str, Any]:
    """Handle broadcast notifications to all connected users."""
    try:
        message_data = notification_data.get('data', {})
        
        # Find all active connections
        connections = find_all_active_connections()
        
        if not connections:
            logger.info("No active connections found for broadcast")
            return create_response(True, "No active connections found", sent_count=0)
        
        # Create notification message
        message = {
            'type': 'broadcast',
            'data': message_data,
            'timestamp': datetime.now(timezone.utc).isoformat()
        }
        
        # Send to all connections
        sent_count = send_to_connections(connections, message)
        
        logger.info(f"Sent broadcast notification to {sent_count} connections")
        return create_response(True, "Broadcast notification sent", sent_count=sent_count)
        
    except Exception as e:
        logger.error(f"Error handling broadcast notification: {e}")
        return create_response(False, f"Failed to send broadcast notification: {str(e)}")


def find_connections_for_job(job_id: str, user_id: str = None) -> List[Dict]:
    """Find connections subscribed to a specific job."""
    try:
        table = dynamodb.Table(CONNECTIONS_TABLE)
        
        # Scan for connections subscribed to this job
        filter_expression = 'subscribed_job_id = :job_id AND attribute_exists(connection_id)'
        expression_values = {':job_id': job_id}
        
        # Optionally filter by user_id if provided
        if user_id:
            filter_expression += ' AND user_id = :user_id'
            expression_values[':user_id'] = user_id
        
        response = table.scan(
            FilterExpression=filter_expression,
            ExpressionAttributeValues=expression_values
        )
        
        return response.get('Items', [])
        
    except Exception as e:
        logger.error(f"Error finding connections for job {job_id}: {e}")
        return []


def find_connections_for_session(session_id: str, user_id: str = None) -> List[Dict]:
    """Find connections subscribed to a specific session."""
    try:
        table = dynamodb.Table(CONNECTIONS_TABLE)
        
        # Scan for connections subscribed to this session
        filter_expression = '(subscribed_session_id = :session_id OR (subscribed_to_session = :subscribed AND subscribed_session_id = :session_id)) AND attribute_exists(connection_id)'
        expression_values = {
            ':session_id': session_id,
            ':subscribed': True
        }
        
        # Optionally filter by user_id if provided
        if user_id:
            filter_expression += ' AND user_id = :user_id'
            expression_values[':user_id'] = user_id
        
        response = table.scan(
            FilterExpression=filter_expression,
            ExpressionAttributeValues=expression_values
        )
        
        return response.get('Items', [])
        
    except Exception as e:
        logger.error(f"Error finding connections for session {session_id}: {e}")
        return []


def find_connections_for_user(user_id: str) -> List[Dict]:
    """Find all active connections for a specific user."""
    try:
        table = dynamodb.Table(CONNECTIONS_TABLE)
        
        # Query using GSI on user_id
        response = table.query(
            IndexName='user-index',
            KeyConditionExpression='user_id = :user_id',
            ExpressionAttributeValues={':user_id': user_id}
        )
        
        return response.get('Items', [])
        
    except Exception as e:
        logger.error(f"Error finding connections for user {user_id}: {e}")
        return []


def find_all_active_connections() -> List[Dict]:
    """Find all active connections."""
    try:
        table = dynamodb.Table(CONNECTIONS_TABLE)
        
        # Scan for all active connections
        response = table.scan(
            FilterExpression='attribute_exists(connection_id) AND #status = :status',
            ExpressionAttributeNames={'#status': 'status'},
            ExpressionAttributeValues={':status': 'connected'}
        )
        
        return response.get('Items', [])
        
    except Exception as e:
        logger.error(f"Error finding all active connections: {e}")
        return []


def send_to_connections(connections: List[Dict], message: Dict) -> int:
    """Send message to multiple WebSocket connections."""
    if not WEBSOCKET_API_ENDPOINT:
        logger.error("WEBSOCKET_API_ENDPOINT not configured")
        return 0
    
    # Create API Gateway Management client
    apigateway_client = boto3.client('apigatewaymanagementapi', endpoint_url=WEBSOCKET_API_ENDPOINT)
    
    sent_count = 0
    stale_connections = []
    
    for connection in connections:
        connection_id = connection.get('connection_id')
        if not connection_id:
            continue
        
        try:
            apigateway_client.post_to_connection(
                ConnectionId=connection_id,
                Data=json.dumps(message)
            )
            sent_count += 1
            logger.debug(f"Sent message to connection {connection_id}")
            
        except ClientError as e:
            error_code = e.response['Error']['Code']
            if error_code == 'GoneException':
                # Connection is stale - mark for cleanup
                stale_connections.append(connection)
                logger.warning(f"Connection {connection_id} is stale, marking for cleanup")
            else:
                logger.error(f"Failed to send message to connection {connection_id}: {e}")
        except Exception as e:
            logger.error(f"Unexpected error sending to connection {connection_id}: {e}")
    
    # Clean up stale connections
    cleanup_stale_connections(stale_connections)
    
    return sent_count


def cleanup_stale_connections(stale_connections: List[Dict]) -> None:
    """Remove stale connections from DynamoDB."""
    if not stale_connections:
        return
    
    try:
        table = dynamodb.Table(CONNECTIONS_TABLE)
        
        for connection in stale_connections:
            table.delete_item(
                Key={
                    'connection_id': connection['connection_id'],
                    'user_id': connection['user_id']
                }
            )
            logger.info(f"Cleaned up stale connection {connection['connection_id']}")
            
    except Exception as e:
        logger.error(f"Error cleaning up stale connections: {e}")


def create_response(success: bool, message: str, **kwargs) -> Dict[str, Any]:
    """Create a standardized response."""
    response = {
        'success': success,
        'message': message,
        'timestamp': datetime.now(timezone.utc).isoformat()
    }
    response.update(kwargs)
    return response


def create_http_response(status_code: int, body: Dict) -> Dict[str, Any]:
    """Create HTTP response for API Gateway."""
    return {
        'statusCode': status_code,
        'headers': {
            'Content-Type': 'application/json',
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Methods': 'POST, OPTIONS',
            'Access-Control-Allow-Headers': 'Content-Type, Authorization'
        },
        'body': json.dumps(body)
    }