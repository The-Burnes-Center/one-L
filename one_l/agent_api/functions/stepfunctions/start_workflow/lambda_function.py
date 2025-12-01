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
        
        # Create initial DynamoDB record so frontend can start polling immediately
        table_name = os.environ.get('ANALYSES_TABLE_NAME')
        if table_name:
            try:
                table = dynamodb.Table(table_name)
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
        
        # Prepare Step Functions input
        sfn_input = {
            'job_id': job_id,
            'session_id': session_id,
            'user_id': user_id,
            'document_s3_key': document_s3_key,
            'bucket_type': bucket_type,
            'terms_profile': terms_profile,
            'timestamp': timestamp_iso  # Use the same timestamp for DynamoDB key
        }
        
        logger.info(f"Starting Step Functions execution with input: {json.dumps(sfn_input)}")
        
        # Start the Step Functions execution
        execution_response = sfn_client.start_execution(
            stateMachineArn=state_machine_arn,
            name=f"review-{job_id}-{uuid.uuid4().hex[:8]}",
            input=json.dumps(sfn_input)
        )
        
        logger.info(f"Step Functions execution started: {execution_response['executionArn']}")
        
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
