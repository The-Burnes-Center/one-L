"""
Shared progress tracking utilities for Step Functions workflow.

Each Lambda in the workflow calls update_progress() to track its stage.
This updates DynamoDB so the frontend can poll for status.
Optionally sends WebSocket notifications for real-time updates.
"""

import boto3
import json
import os
import logging
from datetime import datetime
from typing import Optional, Dict, Any

logger = logging.getLogger()
dynamodb = boto3.resource('dynamodb')
lambda_client = boto3.client('lambda')


# Workflow stages in order with their progress percentages
# These are user-friendly names (internal stages map to these)
STAGES = {
    # Initial stages
    'starting': 2,
    'initialized': 5,
    
    # Document preparation (internal: splitting, etc.)
    'preparing': 10,
    'splitting': 10,
    
    # Document analysis (internal: processing_chunks, merging)
    'analyzing': 25,
    'processing_chunks': 30,
    'merging_results': 40,
    
    # Knowledge base lookup
    'checking_references': 50,
    'retrieving_context': 50,
    
    # Conflict detection
    'finding_conflicts': 60,
    'generating_analysis': 65,
    'identifying_conflicts': 70,
    
    # Redline generation
    'creating_redlines': 80,
    'generating_redlines': 80,
    
    # Final assembly
    'finishing': 90,
    'assembling_document': 90,
    'finalizing': 95,
    
    # Terminal states
    'completed': 100,
    'failed': 0
}


def _send_websocket_notification(job_id: str, session_id: Optional[str], 
                                  user_id: Optional[str], stage: str, 
                                  progress: int, message: Optional[str] = None) -> None:
    """
    Send WebSocket notification for progress update (non-blocking).
    
    Args:
        job_id: The job ID
        session_id: Optional session ID
        user_id: Optional user ID
        stage: Current stage
        progress: Progress percentage
        message: Optional message
    """
    try:
        # Try to get notification function name from environment or construct it
        notification_function_name = os.environ.get('WEBSOCKET_NOTIFICATION_FUNCTION_NAME')
        
        if not notification_function_name:
            # Try to construct from current function name pattern
            current_function = os.environ.get('AWS_LAMBDA_FUNCTION_NAME', '')
            if current_function:
                # Pattern: {stack}-{service}-{function} -> {stack}-websocket-notification
                parts = current_function.split('-')
                if len(parts) >= 2:
                    stack_name = parts[0]
                    notification_function_name = f"{stack_name}-websocket-notification"
        
        if not notification_function_name:
            logger.debug("WebSocket notification function name not available, skipping notification")
            return
        
        notification_payload = {
            'notification_type': 'job_progress',
            'job_id': job_id,
            'session_id': session_id,
            'user_id': user_id,
            'data': {
                'status': 'processing' if stage not in ['completed', 'failed'] else stage,
                'stage': stage,
                'progress': progress,
                'message': message or f"Processing: {stage}"
            }
        }
        
        # Invoke asynchronously (non-blocking)
        lambda_client.invoke(
            FunctionName=notification_function_name,
            InvocationType='Event',
            Payload=json.dumps(notification_payload)
        )
        logger.debug(f"Sent WebSocket notification for job {job_id} at stage {stage}")
        
    except Exception as e:
        # Don't fail progress update if notification fails
        logger.debug(f"Failed to send WebSocket notification (non-critical): {e}")


def update_progress(job_id: str, timestamp: str, stage: str, message: str = None, 
                   extra_data: dict = None, session_id: Optional[str] = None,
                   user_id: Optional[str] = None, send_notification: bool = True) -> bool:
    """
    Update job progress in DynamoDB and optionally send WebSocket notification.
    
    CRITICAL: Always sets both 'status' and 'stage' fields for consistency.
    - 'status' is the primary field: 'processing', 'completed', 'failed'
    - 'stage' is the granular workflow stage: 'analyzing', 'generating_redlines', etc.
    
    Args:
        job_id: The job ID (analysis_id in DynamoDB)
        timestamp: The timestamp (sort key in DynamoDB)
        stage: Current workflow stage (from STAGES)
        message: Optional custom message for this stage
        extra_data: Optional additional data to store
        session_id: Optional session ID for WebSocket notifications
        user_id: Optional user ID for WebSocket notifications
        send_notification: Whether to send WebSocket notification (default: True)
    
    Returns:
        True if update succeeded, False otherwise
    """
    try:
        table_name = os.environ.get('ANALYSES_TABLE_NAME')
        if not table_name:
            logger.warning("ANALYSES_TABLE_NAME not set, skipping progress update")
            return False
        
        table = dynamodb.Table(table_name)
        
        progress = STAGES.get(stage, 0)
        
        # Determine status based on stage
        if stage == 'completed':
            status = 'completed'
        elif stage == 'failed':
            status = 'failed'
        else:
            status = 'processing'  # All other stages are processing
        
        # CRITICAL: Always update both 'status' and 'stage' fields
        update_expr = 'SET #status = :status, stage = :stage, progress = :progress, updated_at = :updated_at'
        expr_values = {
            ':status': status,
            ':stage': stage,
            ':progress': progress,
            ':updated_at': datetime.utcnow().isoformat()
        }
        
        if message:
            update_expr += ', stage_message = :message'
            expr_values[':message'] = message
        
        if extra_data:
            for key, value in extra_data.items():
                safe_key = key.replace('-', '_')
                update_expr += f', {safe_key} = :{safe_key}'
                expr_values[f':{safe_key}'] = value
        
        table.update_item(
            Key={
                'analysis_id': job_id,
                'timestamp': timestamp
            },
            UpdateExpression=update_expr,
            ExpressionAttributeNames={'#status': 'status'},
            ExpressionAttributeValues=expr_values
        )
        
        logger.info(f"Updated job {job_id} to status='{status}', stage='{stage}' ({progress}%)")
        
        # Send WebSocket notification if requested and we have session/user info
        if send_notification and (session_id or user_id):
            _send_websocket_notification(job_id, session_id, user_id, stage, progress, message)
        
        return True
        
    except Exception as e:
        logger.error(f"Failed to update progress: {e}")
        return False


def mark_completed(job_id: str, timestamp: str, result_data: dict = None,
                   session_id: Optional[str] = None, user_id: Optional[str] = None) -> bool:
    """
    Mark a job as completed with optional result data.
    
    Args:
        job_id: The job ID
        timestamp: The timestamp (sort key)
        result_data: Optional result data (redlined_document, analysis, etc.)
        session_id: Optional session ID for WebSocket notifications
        user_id: Optional user ID for WebSocket notifications
    """
    try:
        table_name = os.environ.get('ANALYSES_TABLE_NAME')
        if not table_name:
            logger.warning("ANALYSES_TABLE_NAME not set, skipping completion mark")
            return False
        
        table = dynamodb.Table(table_name)
        
        extra = result_data or {}
        extra['completed_at'] = datetime.utcnow().isoformat()
        
        # CRITICAL: Update both 'status' and 'stage' to 'completed'
        update_expr = 'SET #status = :status, stage = :stage, progress = :progress, updated_at = :updated_at'
        expr_values = {
            ':status': 'completed',
            ':stage': 'completed',
            ':progress': 100,
            ':updated_at': datetime.utcnow().isoformat()
        }
        
        # Add stage message
        update_expr += ', stage_message = :message'
        expr_values[':message'] = 'Document review complete!'
        
        # Add result data
        if extra:
            for key, value in extra.items():
                safe_key = key.replace('-', '_')
                update_expr += f', {safe_key} = :{safe_key}'
                expr_values[f':{safe_key}'] = value
        
        table.update_item(
            Key={
                'analysis_id': job_id,
                'timestamp': timestamp
            },
            UpdateExpression=update_expr,
            ExpressionAttributeNames={'#status': 'status'},
            ExpressionAttributeValues=expr_values
        )
        
        logger.info(f"Marked job {job_id} as completed (status=completed, stage=completed, progress=100%)")
        
        # AUTO-UPDATE SESSION: Increment document_count and update timestamps
        if session_id and user_id:
            try:
                sessions_table_name = os.environ.get('SESSIONS_TABLE')
                if sessions_table_name:
                    sessions_table = dynamodb.Table(sessions_table_name)
                    sessions_table.update_item(
                        Key={
                            'session_id': session_id,
                            'user_id': user_id
                        },
                        UpdateExpression='ADD document_count :inc '
                                        'SET updated_at = :updated, last_activity = :updated, has_results = :has_results',
                        ExpressionAttributeValues={
                            ':inc': 1,
                            ':updated': datetime.utcnow().isoformat(),
                            ':has_results': True
                        }
                    )
                    logger.info(f"Auto-updated session {session_id}: incremented document_count, set has_results=True")
            except Exception as session_update_error:
                logger.warning(f"Failed to auto-update session {session_id}: {session_update_error}")
        
        # Send WebSocket notification for completion
        if session_id or user_id:
            _send_websocket_notification(
                job_id, session_id, user_id, 'completed', 100, 
                'Document review complete!'
            )
        
        return True
        
    except Exception as e:
        logger.error(f"Failed to mark job as completed: {e}")
        return False


def mark_failed(job_id: str, timestamp: str, error_message: str, 
                 session_id: Optional[str] = None, user_id: Optional[str] = None) -> bool:
    """
    Mark a job as failed with an error message.
    
    Args:
        job_id: The job ID
        timestamp: The timestamp (sort key)
        error_message: The error message to store
        session_id: Optional session ID for WebSocket notifications
        user_id: Optional user ID for WebSocket notifications
    """
    return update_progress(
        job_id, timestamp, 'failed', 
        'Processing failed',
        {'error_message': error_message, 'failed_at': datetime.utcnow().isoformat()},
        session_id=session_id,
        user_id=user_id,
        send_notification=True
    )

