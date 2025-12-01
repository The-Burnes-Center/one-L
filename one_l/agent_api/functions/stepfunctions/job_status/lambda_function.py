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
        
        # CRITICAL: Always check Step Functions execution status FIRST before returning
        # This ensures we catch failures even if DynamoDB wasn't updated (e.g., payload size limit errors)
        # Step Functions status is the source of truth for execution state
        execution_arn = item.get('execution_arn')
        sfn_status = None
        sfn_error = None
        sfn_error_details = None
        
        if execution_arn:
            try:
                sfn_response = sfn_client.describe_execution(executionArn=execution_arn)
                sfn_status = sfn_response.get('status')  # RUNNING, SUCCEEDED, FAILED, TIMED_OUT, ABORTED
                
                logger.info(f"Step Functions execution {execution_arn} status: {sfn_status}")
                
                # If Step Functions shows FAILED, TIMED_OUT, or ABORTED, extract error details
                if sfn_status in ['FAILED', 'TIMED_OUT', 'ABORTED']:
                    sfn_error_raw = sfn_response.get('error', 'Unknown error')
                    sfn_cause = sfn_response.get('cause', '')
                    
                    # Extract error message from various formats
                    error_message = None
                    
                    # Try to parse cause as JSON first (most common format)
                    if sfn_cause:
                        try:
                            cause_obj = json.loads(sfn_cause)
                            # Handle nested error structures
                            if isinstance(cause_obj, dict):
                                error_message = (
                                    cause_obj.get('errorMessage') or
                                    cause_obj.get('ErrorMessage') or
                                    cause_obj.get('message') or
                                    cause_obj.get('Message') or
                                    str(cause_obj)
                                )
                            else:
                                error_message = str(cause_obj)
                        except (json.JSONDecodeError, TypeError):
                            # Cause is not JSON, use as-is
                            error_message = str(sfn_cause)
                    
                    # If no error message from cause, use error field
                    if not error_message:
                        if isinstance(sfn_error_raw, dict):
                            error_message = (
                                sfn_error_raw.get('Error') or
                                sfn_error_raw.get('error') or
                                str(sfn_error_raw)
                            )
                        else:
                            error_message = str(sfn_error_raw)
                    
                    # Handle specific error types with user-friendly messages
                    if 'size exceeding the maximum' in error_message.lower() or 'payload size' in error_message.lower():
                        sfn_error = 'The document processing result exceeded the maximum size limit. The document may be too large or contain too much data. Please try with a smaller document or contact support.'
                        sfn_error_details = f"Step Functions Error: {error_message}"
                    elif 'timeout' in error_message.lower() or 'timed out' in error_message.lower():
                        sfn_error = 'Document processing timed out. The document may be too large or complex. Please try with a smaller document or contact support.'
                        sfn_error_details = f"Step Functions Error: {error_message}"
                    else:
                        sfn_error = error_message or f'Step Functions execution {sfn_status}'
                        sfn_error_details = error_message
                    
                    logger.warning(f"Step Functions execution {execution_arn} has status {sfn_status}: {sfn_error}")
                    
                    # CRITICAL: Update DynamoDB to reflect the failed status
                    # This handles edge cases where Step Functions fails but error handler Lambda doesn't catch it
                    try:
                        item_timestamp = item.get('timestamp')
                        if item_timestamp:
                            # Use a more comprehensive error message that includes both user-friendly and technical details
                            full_error_message = sfn_error
                            if sfn_error_details and sfn_error_details != sfn_error:
                                full_error_message = f"{sfn_error} (Technical details: {sfn_error_details})"
                            
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
                                    ':error': full_error_message,
                                    ':updated': datetime.utcnow().isoformat(),
                                    ':progress': 0
                                }
                            )
                            logger.info(f"Updated DynamoDB for job {job_id} to failed status based on Step Functions: {sfn_error}")
                            
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
                        logger.error(f"CRITICAL: Could not update DynamoDB with failed status: {update_error}")
                        # Even if DynamoDB update fails, we still return the failed status from Step Functions
                
            except Exception as sfn_check_error:
                logger.error(f"CRITICAL: Could not check Step Functions status: {sfn_check_error}")
                # If we can't check Step Functions, continue with DynamoDB status
                # But log this as a critical issue
        else:
            logger.warning(f"Job {job_id} has no execution_arn - cannot verify Step Functions status")
        
        # CRITICAL: Step Functions status is the source of truth
        # Always check Step Functions status FIRST, then fall back to DynamoDB
        # This ensures we catch failures even if DynamoDB wasn't updated
        item_status = item.get('status', '').lower()
        item_stage = item.get('stage', '').lower()
        
        # Determine the actual status: Step Functions status takes ABSOLUTE precedence
        if sfn_status in ['FAILED', 'TIMED_OUT', 'ABORTED']:
            # Step Functions shows failure - this is definitive
            # DynamoDB may not be updated yet (e.g., payload size limit errors)
            current_stage = 'failed'
            status = 'failed'
            logger.info(f"Job {job_id} status determined from Step Functions: FAILED (sfn_status={sfn_status})")
        elif sfn_status == 'SUCCEEDED':
            # Step Functions succeeded - BUT check output for internal failures
            # Some Lambdas return success=false without raising exceptions, which Step Functions treats as success
            # CRITICAL: Check the execution output for redline_result.success == false or save_result.success == false
            execution_output = None
            has_internal_failure = False
            internal_error = None
            
            try:
                execution_output_raw = sfn_response.get('output')
                if execution_output_raw:
                    execution_output = json.loads(execution_output_raw)
                    
                    # Check redline_result for failures
                    redline_result = execution_output.get('redline_result', {})
                    if isinstance(redline_result, dict) and redline_result.get('success') is False:
                        has_internal_failure = True
                        internal_error = redline_result.get('error', 'Redline generation failed')
                        logger.warning(f"Job {job_id} Step Functions SUCCEEDED but redline_result.success=false: {internal_error}")
                    
                    # Check save_result for failures
                    save_result = execution_output.get('save_result', {})
                    if isinstance(save_result, dict) and save_result.get('success') is False:
                        has_internal_failure = True
                        save_error = save_result.get('error', 'Save results failed')
                        if internal_error:
                            internal_error = f"{internal_error}; {save_error}"
                        else:
                            internal_error = save_error
                        logger.warning(f"Job {job_id} Step Functions SUCCEEDED but save_result.success=false: {save_error}")
            except (json.JSONDecodeError, TypeError, AttributeError) as parse_error:
                logger.warning(f"Could not parse Step Functions output for job {job_id}: {parse_error}")
                # If we can't parse output, assume success (may be incomplete execution)
            
            if has_internal_failure:
                # Internal failure detected - mark as failed
                current_stage = 'failed'
                status = 'failed'
                sfn_error = internal_error or 'An internal error occurred during document processing'
                sfn_error_details = f"Step Functions execution completed but internal step failed: {internal_error}"
                
                logger.warning(f"Job {job_id} has internal failure despite Step Functions SUCCEEDED: {sfn_error}")
                
                # Update DynamoDB to reflect the failed status
                try:
                    item_timestamp = item.get('timestamp')
                    if item_timestamp:
                        full_error_message = sfn_error
                        if sfn_error_details and sfn_error_details != sfn_error:
                            full_error_message = f"{sfn_error} (Technical details: {sfn_error_details})"
                        
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
                                ':error': full_error_message,
                                ':updated': datetime.utcnow().isoformat(),
                                ':progress': 0
                            }
                        )
                        logger.info(f"Updated DynamoDB for job {job_id} to failed status based on internal failure in Step Functions output")
                        
                        # Re-read the item to get updated values
                        updated_response = table.get_item(
                            Key={
                                'analysis_id': job_id,
                                'timestamp': item_timestamp
                            }
                        )
                        if updated_response.get('Item'):
                            item = updated_response['Item']
                            item_status = 'failed'
                except Exception as update_error:
                    logger.error(f"CRITICAL: Could not update DynamoDB with failed status: {update_error}")
            else:
                # Step Functions succeeded and no internal failures - mark as completed
                current_stage = 'completed'
                status = 'completed'
                
                # CRITICAL: Update DynamoDB to reflect completion if not already updated
                if item_status != 'completed':
                    try:
                        item_timestamp = item.get('timestamp')
                        if item_timestamp:
                            table.update_item(
                                Key={
                                    'analysis_id': job_id,
                                    'timestamp': item_timestamp
                                },
                                UpdateExpression='SET #status = :status, stage = :stage, progress = :progress, updated_at = :updated',
                                ExpressionAttributeNames={'#status': 'status'},
                                ExpressionAttributeValues={
                                    ':status': 'completed',
                                    ':stage': 'completed',
                                    ':progress': 100,
                                    ':updated': datetime.utcnow().isoformat()
                                }
                            )
                            logger.info(f"Updated DynamoDB for job {job_id} to completed status based on Step Functions SUCCEEDED")
                            
                            # Re-read the item to get updated values
                            updated_response = table.get_item(
                                Key={
                                    'analysis_id': job_id,
                                    'timestamp': item_timestamp
                                }
                            )
                            if updated_response.get('Item'):
                                item = updated_response['Item']
                                item_status = 'completed'
                    except Exception as update_error:
                        logger.warning(f"Could not update DynamoDB with completed status: {update_error}")
                
                logger.info(f"Job {job_id} status determined from Step Functions: SUCCEEDED -> COMPLETED")
        elif sfn_status == 'RUNNING':
            # Step Functions is still running - use DynamoDB status/stage for progress
            if item_status == 'failed':
                # DynamoDB says failed but Step Functions is running - trust Step Functions (may be stale DynamoDB)
                current_stage = item_stage or 'processing'
                status = 'processing'
                logger.warning(f"Job {job_id} DynamoDB says failed but Step Functions is RUNNING - trusting Step Functions")
            else:
                current_stage = item_stage or item_status or 'initialized'
                status = 'processing'
        elif item_status == 'failed':
            # DynamoDB says failed but no Step Functions status - trust DynamoDB
            current_stage = 'failed'
            status = 'failed'
            logger.warning(f"Job {job_id} status determined from DynamoDB only (no Step Functions status): FAILED")
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
            # Prefer Step Functions error (most accurate), then DynamoDB error_message, then stage_message
            error_msg = sfn_error or item.get('error_message') or item.get('stage_message') or 'Unknown error occurred'
            result['error'] = error_msg
            result['error_message'] = error_msg
            
            # Log the error source for debugging
            if sfn_error:
                logger.info(f"Job {job_id} error from Step Functions: {sfn_error}")
            elif item.get('error_message'):
                logger.info(f"Job {job_id} error from DynamoDB: {item.get('error_message')}")
            else:
                logger.warning(f"Job {job_id} failed but no error message found")
        
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

