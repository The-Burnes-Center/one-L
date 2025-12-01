"""
Job Status Lambda - Returns current status of a document review job.

This Lambda is called by the frontend to poll for job progress.
It reads from DynamoDB to get the latest status.
"""

import json
import boto3
import logging
import os
from datetime import datetime
from decimal import Decimal

logger = logging.getLogger()
logger.setLevel(logging.INFO)

dynamodb = boto3.resource('dynamodb')

# Define the workflow stages with their progress percentages
WORKFLOW_STAGES = {
    'initialized': {'progress': 5, 'label': 'Initializing', 'description': 'Setting up document review...'},
    'splitting': {'progress': 10, 'label': 'Splitting Document', 'description': 'Breaking document into sections...'},
    'processing_chunks': {'progress': 25, 'label': 'Processing Sections', 'description': 'Analyzing document sections...'},
    'merging_results': {'progress': 40, 'label': 'Merging Results', 'description': 'Combining analysis results...'},
    'retrieving_context': {'progress': 50, 'label': 'Retrieving Context', 'description': 'Searching knowledge base...'},
    'generating_analysis': {'progress': 60, 'label': 'Generating Analysis', 'description': 'AI is analyzing for conflicts...'},
    'identifying_conflicts': {'progress': 70, 'label': 'Identifying Conflicts', 'description': 'Finding contract conflicts...'},
    'generating_redlines': {'progress': 80, 'label': 'Generating Redlines', 'description': 'Creating redlined version...'},
    'assembling_document': {'progress': 90, 'label': 'Assembling Document', 'description': 'Building final document...'},
    'finalizing': {'progress': 95, 'label': 'Finalizing', 'description': 'Wrapping up...'},
    'completed': {'progress': 100, 'label': 'Complete', 'description': 'Document review complete!'},
    'failed': {'progress': 0, 'label': 'Failed', 'description': 'An error occurred during processing.'}
}

def decimal_default(obj):
    """JSON serializer for Decimal objects from DynamoDB."""
    if isinstance(obj, Decimal):
        return float(obj)
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")


def lambda_handler(event, context):
    """
    Get the status of a document review job.
    
    Query parameters:
    - job_id: The job ID to check status for
    
    Returns job status including:
    - stage: Current workflow stage
    - progress: Percentage complete (0-100)
    - label: Human-readable stage name
    - description: Detailed description of current stage
    - status: 'processing', 'completed', 'failed'
    - result: Final result data (if completed)
    - error: Error message (if failed)
    """
    try:
        # Get job_id from path or query parameters
        job_id = None
        
        # Try path parameters first
        if event.get('pathParameters'):
            job_id = event['pathParameters'].get('job_id')
        
        # Try query parameters
        if not job_id and event.get('queryStringParameters'):
            job_id = event['queryStringParameters'].get('job_id')
        
        # Try request body
        if not job_id:
            body = event.get('body', '{}')
            if isinstance(body, str):
                body = json.loads(body)
            job_id = body.get('job_id')
        
        if not job_id:
            return {
                'statusCode': 400,
                'headers': cors_headers(),
                'body': json.dumps({
                    'success': False,
                    'error': 'job_id is required'
                })
            }
        
        logger.info(f"Getting status for job: {job_id}")
        
        # Query DynamoDB for job status
        table_name = os.environ.get('ANALYSES_TABLE_NAME')
        if not table_name:
            return {
                'statusCode': 500,
                'headers': cors_headers(),
                'body': json.dumps({
                    'success': False,
                    'error': 'Service not configured properly'
                })
            }
        
        table = dynamodb.Table(table_name)
        
        # Query for items with this job_id (analysis_id)
        # Since we have a composite key, we need to query by the partition key
        response = table.query(
            KeyConditionExpression='analysis_id = :job_id',
            ExpressionAttributeValues={
                ':job_id': job_id
            },
            ScanIndexForward=False,  # Get most recent first
            Limit=1
        )
        
        if not response.get('Items'):
            return {
                'statusCode': 404,
                'headers': cors_headers(),
                'body': json.dumps({
                    'success': False,
                    'error': 'Job not found',
                    'job_id': job_id
                })
            }
        
        item = response['Items'][0]
        
        # Get stage info
        current_stage = item.get('stage', item.get('status', 'initialized'))
        stage_info = WORKFLOW_STAGES.get(current_stage, WORKFLOW_STAGES['initialized'])
        
        # Build response
        result = {
            'success': True,
            'job_id': job_id,
            'stage': current_stage,
            'progress': stage_info['progress'],
            'label': stage_info['label'],
            'description': item.get('stage_message') or stage_info['description'],
            'status': 'completed' if current_stage == 'completed' else ('failed' if current_stage == 'failed' else 'processing'),
            'updated_at': item.get('updated_at') or item.get('timestamp'),
            'session_id': item.get('session_id'),
            'chunks_processed': item.get('chunks_processed', 0),
            'total_chunks': item.get('total_chunks', 0)
        }
        
        # Add result data if completed
        if current_stage == 'completed':
            result['result'] = {
                'redlined_document': item.get('redlined_document'),
                'analysis': item.get('analysis'),
                'conflicts_found': item.get('conflicts_found', 0),
                'has_redlines': bool(item.get('redlined_document'))
            }
        
        # Add error info if failed
        if current_stage == 'failed':
            result['error'] = item.get('error_message') or 'Unknown error occurred'
        
        return {
            'statusCode': 200,
            'headers': cors_headers(),
            'body': json.dumps(result, default=decimal_default)
        }
        
    except Exception as e:
        logger.error(f"Error getting job status: {str(e)}")
        return {
            'statusCode': 500,
            'headers': cors_headers(),
            'body': json.dumps({
                'success': False,
                'error': f'Failed to get job status: {str(e)}'
            })
        }


def cors_headers():
    """Return CORS headers for API Gateway."""
    return {
        'Content-Type': 'application/json',
        'Access-Control-Allow-Origin': '*',
        'Access-Control-Allow-Headers': 'Content-Type,X-Amz-Date,Authorization,X-Api-Key,X-Amz-Security-Token',
        'Access-Control-Allow-Methods': 'GET,POST,OPTIONS'
    }

