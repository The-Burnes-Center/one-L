"""
Start Workflow Lambda - Wrapper for Step Functions invocation.

This Lambda handles the API request, generates a job_id, starts the Step Functions
execution, and returns a properly formatted response to the frontend.

It also creates the initial DynamoDB record so the frontend can immediately
start polling for status.
"""

import json
import boto3
import logging
import os
from datetime import datetime
import uuid

logger = logging.getLogger()
logger.setLevel(logging.INFO)

sfn_client = boto3.client('stepfunctions')
dynamodb = boto3.resource('dynamodb')

def lambda_handler(event, context):
    """
    Start the document review workflow via Step Functions.
    
    This wrapper:
    1. Parses the incoming request
    2. Generates a job_id
    3. Starts the Step Functions execution with the job_id
    4. Returns a response the frontend expects
    """
    try:
        # Parse the request body
        body = event.get('body', '{}')
        if isinstance(body, str):
            body = json.loads(body)
        
        logger.info(f"Received request: {json.dumps(body)}")
        
        # Extract required fields
        document_s3_key = body.get('document_s3_key')
        bucket_type = body.get('bucket_type', 'agent_processing')
        session_id = body.get('session_id')
        user_id = body.get('user_id')
        terms_profile = body.get('terms_profile', 'it')
        
        # Validate required fields
        if not document_s3_key:
            return {
                'statusCode': 400,
                'headers': {
                    'Content-Type': 'application/json',
                    'Access-Control-Allow-Origin': '*',
                    'Access-Control-Allow-Headers': 'Content-Type,X-Amz-Date,Authorization,X-Api-Key,X-Amz-Security-Token'
                },
                'body': json.dumps({
                    'success': False,
                    'error': 'document_s3_key is required'
                })
            }
        
        if not session_id or not user_id:
            return {
                'statusCode': 400,
                'headers': {
                    'Content-Type': 'application/json',
                    'Access-Control-Allow-Origin': '*',
                    'Access-Control-Allow-Headers': 'Content-Type,X-Amz-Date,Authorization,X-Api-Key,X-Amz-Security-Token'
                },
                'body': json.dumps({
                    'success': False,
                    'error': 'session_id and user_id are required'
                })
            }
        
        # Generate job_id and timestamp
        timestamp = datetime.utcnow()
        timestamp_iso = timestamp.isoformat()
        job_id = f"{session_id}_{int(timestamp.timestamp() * 1000)}"
        
        # Get table reference for cleanup and new job creation
        table_name = os.environ.get('ANALYSES_TABLE_NAME')
        if table_name:
            try:
                table = dynamodb.Table(table_name)
                
                # BACKEND CLEANUP: Cancel/cleanup old processing jobs for this session
                # Each session should only have 1 active job at a time
                logger.info(f"Cleaning up old processing jobs for session {session_id} before starting new job {job_id}")
                
                # Scan for all active jobs for this session
                response = table.scan(
                    FilterExpression='session_id = :session_id AND user_id = :user_id',
                    ExpressionAttributeValues={
                        ':session_id': session_id,
                        ':user_id': user_id
                    }
                )
                
                old_jobs = []
                for item in response.get('Items', []):
                    status = item.get('status', '').lower()
                    stage = item.get('stage', '').lower()
                    has_redlines = bool(item.get('redlined_document_s3_key'))
                    old_job_id = item.get('analysis_id')
                    
                    # Identify active/processing jobs (not completed)
                    is_active = (
                        status in ['processing', 'initialized', 'starting'] or
                        (status not in ['completed', 'failed'] and stage not in ['completed', 'failed'] and not has_redlines)
                    )
                    
                    if is_active and old_job_id != job_id:
                        old_jobs.append({
                            'analysis_id': old_job_id,
                            'timestamp': item.get('timestamp'),
                            'execution_arn': item.get('execution_arn')
                        })
                
                # Cancel old processing jobs
                for old_job in old_jobs:
                    old_job_id = old_job['analysis_id']
                    old_timestamp = old_job['timestamp']
                    execution_arn = old_job.get('execution_arn')
                    
                    try:
                        # Update DynamoDB to mark old job as cancelled/replaced
                        table.update_item(
                            Key={
                                'analysis_id': old_job_id,
                                'timestamp': old_timestamp
                            },
                            UpdateExpression='SET #status = :status, stage = :stage, updated_at = :updated, error_message = :error',
                            ExpressionAttributeNames={'#status': 'status'},
                            ExpressionAttributeValues={
                                ':status': 'failed',
                                ':stage': 'cancelled',
                                ':updated': timestamp_iso,
                                ':error': 'Job cancelled: A new job was started for this session'
                            }
                        )
                        logger.info(f"Marked old job {old_job_id} as cancelled in DynamoDB")
                        
                        # Try to stop Step Functions execution if it's still running
                        if execution_arn:
                            try:
                                sfn_client.stop_execution(
                                    executionArn=execution_arn,
                                    error='JobCancelled',
                                    cause='A new job was started for this session. Only one job per session is allowed.'
                                )
                                logger.info(f"Stopped Step Functions execution {execution_arn} for old job {old_job_id}")
                            except sfn_client.exceptions.ExecutionDoesNotExist:
                                logger.info(f"Step Functions execution {execution_arn} already completed or doesn't exist")
                            except Exception as sfn_error:
                                logger.warning(f"Could not stop Step Functions execution {execution_arn}: {sfn_error}")
                    except Exception as cleanup_error:
                        logger.warning(f"Could not cleanup old job {old_job_id}: {cleanup_error}")
                
                if old_jobs:
                    logger.info(f"Cleaned up {len(old_jobs)} old processing job(s) for session {session_id}")
                
                # Update session title to use the document filename
                # Extract filename from document_s3_key (e.g., "vendor-submissions/uuid_filename.docx" -> "filename.docx")
                # Remove UUID prefix if present (format: uuid_filename.docx)
                raw_filename = document_s3_key.split('/')[-1] if document_s3_key else None
                if raw_filename:
                    # Remove UUID prefix if present (format: uuid_filename.docx -> filename.docx)
                    import re
                    match = re.match(r'^[a-f0-9-]+_(.+)$', raw_filename, re.IGNORECASE)
                    filename = match.group(1) if match else raw_filename
                else:
                    filename = None
                if filename:
                    try:
                        sessions_table_name = os.environ.get('SESSIONS_TABLE')
                        if sessions_table_name:
                            sessions_table = dynamodb.Table(sessions_table_name)
                            sessions_table.update_item(
                                Key={
                                    'session_id': session_id,
                                    'user_id': user_id
                                },
                                UpdateExpression='SET title = :title, updated_at = :updated_at, last_activity = :last_activity',
                                ExpressionAttributeValues={
                                    ':title': filename,
                                    ':updated_at': timestamp_iso,
                                    ':last_activity': timestamp_iso
                                }
                            )
                            logger.info(f"Updated session {session_id} title to: {filename}")
                        else:
                            logger.warning("SESSIONS_TABLE environment variable not set, skipping session title update")
                    except Exception as title_update_error:
                        # Non-fatal: log but don't fail the workflow
                        logger.warning(f"Could not update session title: {title_update_error}")
                
                # Create initial DynamoDB record for new job
                table.put_item(
                    Item={
                        'analysis_id': job_id,
                        'timestamp': timestamp_iso,
                        'session_id': session_id,
                        'user_id': user_id,
                        'document_s3_key': document_s3_key,
                        'bucket_type': bucket_type,
                        'terms_profile': terms_profile,
                        'status': 'starting',
                        'stage': 'starting',
                        'progress': 0,
                        'stage_message': 'Starting document review workflow...',
                        'created_at': timestamp_iso,
                        'updated_at': timestamp_iso
                    }
                )
                logger.info(f"Created initial DynamoDB record for job {job_id}")
            except Exception as db_error:
                logger.warning(f"Could not create DynamoDB record: {db_error}")
        
        # Get state machine ARN from environment
        state_machine_arn = os.environ.get('STATE_MACHINE_ARN')
        if not state_machine_arn:
            logger.error("STATE_MACHINE_ARN environment variable not set")
            return {
                'statusCode': 500,
                'headers': {
                    'Content-Type': 'application/json',
                    'Access-Control-Allow-Origin': '*',
                    'Access-Control-Allow-Headers': 'Content-Type,X-Amz-Date,Authorization,X-Api-Key,X-Amz-Security-Token'
                },
                'body': json.dumps({
                    'success': False,
                    'error': 'Workflow not configured properly'
                })
            }
        
        # Get knowledge_base_id and region from environment variables
        # These are set by the CDK construct when creating the Lambda
        knowledge_base_id = os.environ.get('KNOWLEDGE_BASE_ID')
        region = os.environ.get('REGION', 'us-east-1')
        
        if not knowledge_base_id:
            logger.warning("KNOWLEDGE_BASE_ID not set in environment, workflow may fail")
        
        # Prepare Step Functions input
        sfn_input = {
            'job_id': job_id,
            'session_id': session_id,
            'user_id': user_id,
            'document_s3_key': document_s3_key,
            'bucket_type': bucket_type,
            'terms_profile': terms_profile,
            'timestamp': timestamp_iso,  # Use the same timestamp for DynamoDB key
            'knowledge_base_id': knowledge_base_id,  # Required for KB queries
            'region': region  # Required for KB queries
        }
        
        logger.info(f"Starting Step Functions execution with input: {json.dumps(sfn_input)}")
        
        # Start the Step Functions execution
        execution_response = sfn_client.start_execution(
            stateMachineArn=state_machine_arn,
            name=f"review-{job_id}-{uuid.uuid4().hex[:8]}",
            input=json.dumps(sfn_input)
        )
        
        execution_arn = execution_response['executionArn']
        logger.info(f"Step Functions execution started: {execution_arn}")
        
        # Update DynamoDB record with execution_arn for job_status Lambda to query Step Functions
        if table_name:
            try:
                table = dynamodb.Table(table_name)
                table.update_item(
                    Key={
                        'analysis_id': job_id,
                        'timestamp': timestamp_iso
                    },
                    UpdateExpression='SET execution_arn = :arn, updated_at = :updated',
                    ExpressionAttributeValues={
                        ':arn': execution_arn,
                        ':updated': timestamp_iso
                    }
                )
                logger.info(f"Updated DynamoDB record with execution_arn for job {job_id}")
            except Exception as db_error:
                logger.warning(f"Could not update DynamoDB with execution_arn: {db_error}")
        
        # Return success response in the format the frontend expects
        return {
            'statusCode': 200,
            'headers': {
                'Content-Type': 'application/json',
                'Access-Control-Allow-Origin': '*',
                'Access-Control-Allow-Headers': 'Content-Type,X-Amz-Date,Authorization,X-Api-Key,X-Amz-Security-Token'
            },
            'body': json.dumps({
                'success': True,
                'processing': True,
                'job_id': job_id,
                'execution_arn': execution_response['executionArn'],
                'message': 'Document review workflow started. Processing in background.',
                'status': 'processing'
            })
        }
        
    except sfn_client.exceptions.StateMachineDoesNotExist:
        logger.error("State machine does not exist")
        return {
            'statusCode': 500,
            'headers': {
                'Content-Type': 'application/json',
                'Access-Control-Allow-Origin': '*',
                'Access-Control-Allow-Headers': 'Content-Type,X-Amz-Date,Authorization,X-Api-Key,X-Amz-Security-Token'
            },
            'body': json.dumps({
                'success': False,
                'error': 'Workflow service unavailable'
            })
        }
    except Exception as e:
        logger.error(f"Error starting workflow: {str(e)}")
        return {
            'statusCode': 500,
            'headers': {
                'Content-Type': 'application/json',
                'Access-Control-Allow-Origin': '*',
                'Access-Control-Allow-Headers': 'Content-Type,X-Amz-Date,Authorization,X-Api-Key,X-Amz-Security-Token'
            },
            'body': json.dumps({
                'success': False,
                'error': f'Failed to start workflow: {str(e)}'
            })
        }
