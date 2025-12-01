"""
Shared progress tracking utilities for Step Functions workflow.

Each Lambda in the workflow calls update_progress() to track its stage.
This updates DynamoDB so the frontend can poll for status.
"""

import boto3
import os
import logging
from datetime import datetime

logger = logging.getLogger()
dynamodb = boto3.resource('dynamodb')


# Workflow stages in order with their progress percentages
STAGES = {
    'initialized': 5,
    'splitting': 10,
    'processing_chunks': 25,
    'merging_results': 40,
    'retrieving_context': 50,
    'generating_analysis': 60,
    'identifying_conflicts': 70,
    'generating_redlines': 80,
    'assembling_document': 90,
    'finalizing': 95,
    'completed': 100,
    'failed': 0
}


def update_progress(job_id: str, timestamp: str, stage: str, message: str = None, 
                   extra_data: dict = None) -> bool:
    """
    Update job progress in DynamoDB.
    
    Args:
        job_id: The job ID (analysis_id in DynamoDB)
        timestamp: The timestamp (sort key in DynamoDB)
        stage: Current workflow stage (from STAGES)
        message: Optional custom message for this stage
        extra_data: Optional additional data to store
    
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
        
        update_expr = 'SET stage = :stage, progress = :progress, updated_at = :updated_at'
        expr_values = {
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
            ExpressionAttributeValues=expr_values
        )
        
        logger.info(f"Updated job {job_id} to stage '{stage}' ({progress}%)")
        return True
        
    except Exception as e:
        logger.error(f"Failed to update progress: {e}")
        return False


def mark_completed(job_id: str, timestamp: str, result_data: dict = None) -> bool:
    """
    Mark a job as completed with optional result data.
    
    Args:
        job_id: The job ID
        timestamp: The timestamp (sort key)
        result_data: Optional result data (redlined_document, analysis, etc.)
    """
    extra = result_data or {}
    extra['completed_at'] = datetime.utcnow().isoformat()
    return update_progress(job_id, timestamp, 'completed', 'Document review complete!', extra)


def mark_failed(job_id: str, timestamp: str, error_message: str) -> bool:
    """
    Mark a job as failed with an error message.
    
    Args:
        job_id: The job ID
        timestamp: The timestamp (sort key)
        error_message: The error message to store
    """
    return update_progress(
        job_id, timestamp, 'failed', 
        'Processing failed',
        {'error_message': error_message, 'failed_at': datetime.utcnow().isoformat()}
    )

