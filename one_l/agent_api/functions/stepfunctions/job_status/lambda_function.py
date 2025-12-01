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
sfn_client = boto3.client('stepfunctions')

# Define the workflow stages with user-friendly labels and descriptions
# Internal stages are mapped to user-facing descriptions
WORKFLOW_STAGES = {
    # Initial stages
    'starting': {'progress': 2, 'label': 'Starting', 'description': 'Initializing document review...'},
    'initialized': {'progress': 5, 'label': 'Starting', 'description': 'Preparing to analyze your document...'},
    
    # Document preparation
    'preparing': {'progress': 10, 'label': 'Preparing', 'description': 'Preparing your document for analysis...'},
    'splitting': {'progress': 10, 'label': 'Preparing', 'description': 'Preparing your document for analysis...'},
    
    # Document analysis (these are the heavy lifting stages)
    'analyzing': {'progress': 25, 'label': 'Analyzing', 'description': 'AI is reading through your document...'},
    'processing_chunks': {'progress': 30, 'label': 'Analyzing', 'description': 'AI is analyzing document content...'},
    'merging_results': {'progress': 40, 'label': 'Analyzing', 'description': 'Processing analysis results...'},
    
    # Knowledge base lookup
    'checking_references': {'progress': 50, 'label': 'Checking References', 'description': 'Comparing against your reference documents...'},
    'retrieving_context': {'progress': 50, 'label': 'Checking References', 'description': 'Searching knowledge base for relevant clauses...'},
    
    # Conflict detection
    'finding_conflicts': {'progress': 60, 'label': 'Finding Conflicts', 'description': 'Identifying potential contract issues...'},
    'generating_analysis': {'progress': 65, 'label': 'Finding Conflicts', 'description': 'AI is detecting conflicts and discrepancies...'},
    'identifying_conflicts': {'progress': 70, 'label': 'Finding Conflicts', 'description': 'Cataloging all identified conflicts...'},
    
    # Redline generation
    'creating_redlines': {'progress': 80, 'label': 'Creating Redlines', 'description': 'Generating tracked changes for conflicts...'},
    'generating_redlines': {'progress': 80, 'label': 'Creating Redlines', 'description': 'Creating your redlined document...'},
    
    # Final assembly
    'finishing': {'progress': 90, 'label': 'Finishing', 'description': 'Assembling your final document...'},
    'assembling_document': {'progress': 90, 'label': 'Finishing', 'description': 'Building the final redlined document...'},
    'finalizing': {'progress': 95, 'label': 'Finishing', 'description': 'Almost done! Saving your results...'},
    
    # Terminal states
    'completed': {'progress': 100, 'label': 'Complete', 'description': 'Document review complete! Your redlined document is ready.'},
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
        
        # Check Step Functions execution status if execution_arn is available
        # This ensures we catch failures even if DynamoDB wasn't updated
        execution_arn = item.get('execution_arn')
        sfn_status = None
        sfn_error = None
        
        if execution_arn:
            try:
                sfn_response = sfn_client.describe_execution(executionArn=execution_arn)
                sfn_status = sfn_response.get('status')  # RUNNING, SUCCEEDED, FAILED, TIMED_OUT, ABORTED
                
                logger.info(f"Step Functions execution {execution_arn} status: {sfn_status}")
                
                # If Step Functions shows FAILED or TIMED_OUT, get error details
                if sfn_status in ['FAILED', 'TIMED_OUT', 'ABORTED']:
                    sfn_error = sfn_response.get('error', 'Unknown error')
                    sfn_cause = sfn_response.get('cause', '')
                    
                    # Try to parse cause as JSON to extract error message
                    if sfn_cause:
                        try:
                            cause_obj = json.loads(sfn_cause)
                            sfn_error = cause_obj.get('errorMessage', sfn_cause)
                        except:
                            sfn_error = sfn_cause if sfn_cause else sfn_error
                    
                    logger.warning(f"Step Functions execution {execution_arn} has status {sfn_status}: {sfn_error}")
                    
                    # Update DynamoDB to reflect the failed status
                    try:
                        item_timestamp = item.get('timestamp')
                        if item_timestamp:
                            table.update_item(
                                Key={
                                    'analysis_id': job_id,
                                    'timestamp': item_timestamp
                                },
                                UpdateExpression='SET #status = :status, stage = :stage, error_message = :error, updated_at = :updated, progress = :progress',
                                ExpressionAttributeNames={'#status': 'status'},
                                ExpressionAttributeValues={
                                    ':status': 'failed',
                                    ':stage': 'failed',
                                    ':error': sfn_error or f'Step Functions execution {sfn_status}',
                                    ':updated': datetime.utcnow().isoformat(),
                                    ':progress': 0
                                }
                            )
                            logger.info(f"Updated DynamoDB for job {job_id} to failed status based on Step Functions")
                            
                            # Re-read the item to get updated values
                            updated_response = table.get_item(
                                Key={
                                    'analysis_id': job_id,
                                    'timestamp': item_timestamp
                                }
                            )
                            if updated_response.get('Item'):
                                item = updated_response['Item']
                        else:
                            logger.warning(f"Could not update DynamoDB: timestamp missing for job {job_id}")
                    except Exception as update_error:
                        logger.warning(f"Could not update DynamoDB with failed status: {update_error}")
                
            except Exception as sfn_error:
                logger.warning(f"Could not check Step Functions status: {sfn_error}")
                # Continue with DynamoDB status if Step Functions check fails
        
        # Check status field first (it's the source of truth)
        # Status takes precedence over stage for terminal states
        item_status = item.get('status', '').lower()
        item_stage = item.get('stage', '').lower()
        
        # Determine the actual status: Step Functions status takes precedence, then DynamoDB status
        if sfn_status in ['FAILED', 'TIMED_OUT', 'ABORTED']:
            # Step Functions shows failure - use that
            current_stage = 'failed'
            status = 'failed'
        elif item_status == 'failed':
            current_stage = 'failed'
            status = 'failed'
        elif sfn_status == 'SUCCEEDED':
            # Step Functions succeeded - check if DynamoDB has results
            if item_status == 'completed' and item.get('redlined_document'):
                current_stage = 'completed'
                status = 'completed'
            else:
                # Step Functions succeeded but DynamoDB not updated yet - mark as completed
                # This handles the case where Step Functions finished but DynamoDB update is delayed
                current_stage = 'completed'
                status = 'completed'
        elif item_status == 'completed':
            current_stage = 'completed'
            status = 'completed'
        else:
            # Use stage for processing states
            current_stage = item_stage or item_status or 'initialized'
            status = 'processing'
        
        stage_info = WORKFLOW_STAGES.get(current_stage, WORKFLOW_STAGES['initialized'])
        
        # Get progress from DynamoDB if available, otherwise use stage default
        # Use actual progress from DynamoDB if it exists (from progress_tracker)
        item_progress = item.get('progress')
        if item_progress is not None:
            progress_value = int(item_progress)
        else:
            progress_value = stage_info['progress'] if status != 'failed' else 0
        
        # Build response
        # Ensure progress is always an integer (not float from Decimal conversion)
        progress_value_int = int(progress_value) if progress_value is not None else 0
        
        result = {
            'success': True,
            'job_id': job_id,
            'stage': current_stage,
            'progress': progress_value_int,  # Always integer for frontend
            'label': stage_info['label'],
            'status': status,  # Use the determined status: 'processing', 'completed', or 'failed'
            'updated_at': item.get('updated_at') or item.get('timestamp'),
            'session_id': item.get('session_id'),
            'document_s3_key': item.get('document_s3_key'),  # Include document key for frontend
            'chunks_processed': item.get('chunks_processed', 0),
            'total_chunks': item.get('total_chunks', 0)
        }
        
        logger.info(f"Returning job status for {job_id}: status={status}, stage={current_stage}, progress={progress_value_int} (type={type(progress_value_int).__name__})")
        
        # Add result data if completed
        if current_stage == 'completed':
            result['result'] = {
                'redlined_document': item.get('redlined_document'),
                'analysis': item.get('analysis'),
                'conflicts_found': item.get('conflicts_found', 0),
                'has_redlines': bool(item.get('redlined_document'))
            }
        
        # Add error info if failed
        if status == 'failed':
            # Prefer Step Functions error, then DynamoDB error_message, then stage_message
            error_msg = sfn_error or item.get('error_message') or item.get('stage_message') or 'Unknown error occurred'
            result['error'] = error_msg
            result['error_message'] = error_msg
        
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

