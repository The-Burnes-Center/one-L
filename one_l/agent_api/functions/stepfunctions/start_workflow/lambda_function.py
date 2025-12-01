"""
Start workflow Lambda function.
This wrapper generates a job_id upfront and starts Step Functions,
returning the job_id immediately so the frontend can poll for results.
"""

import json
import boto3
import logging
import os
import uuid
from datetime import datetime

logger = logging.getLogger()
logger.setLevel(logging.INFO)

sfn_client = boto3.client('stepfunctions')
dynamodb = boto3.resource('dynamodb')

def lambda_handler(event, context):
    """
    Start the document review workflow.
    
    1. Generate job_id upfront
    2. Save initial status to DynamoDB
    3. Start Step Functions with job_id
    4. Return job_id immediately to frontend
    
    Args:
        event: API Gateway event with document_s3_key, session_id, user_id, terms_profile
        context: Lambda context
        
    Returns:
        { job_id, status, message } - Frontend can use job_id to poll for results
    """
    try:
        # Parse body if it's a string (from API Gateway)
        if isinstance(event.get('body'), str):
            body = json.loads(event['body'])
        else:
            body = event.get('body', event)
        
        # Extract parameters
        session_id = body.get('session_id')
        user_id = body.get('user_id')
        document_s3_key = body.get('document_s3_key')
        bucket_type = body.get('bucket_type', 'agent_processing')
        terms_profile = body.get('terms_profile', 'general')
        
        if not session_id or not user_id or not document_s3_key:
            return {
                "statusCode": 400,
                "headers": {
                    "Content-Type": "application/json",
                    "Access-Control-Allow-Origin": "*"
                },
                "body": json.dumps({
                    "error": "session_id, user_id, and document_s3_key are required"
                })
            }
        
        # Generate job_id upfront - this is the key!
        timestamp = datetime.utcnow().isoformat()
        job_id = f"{session_id}_{int(datetime.now().timestamp() * 1000)}"
        
        logger.info(f"Generated job_id: {job_id} for session: {session_id}")
        
        # Save initial status to DynamoDB immediately
        table_name = os.environ.get('ANALYSES_TABLE_NAME')
        if table_name:
            table = dynamodb.Table(table_name)
            table.put_item(
                Item={
                    'analysis_id': job_id,
                    'timestamp': timestamp,
                    'session_id': session_id,
                    'user_id': user_id,
                    'status': 'started',
                    'created_at': timestamp,
                    'document_s3_key': document_s3_key,
                    'processing': True
                }
            )
            logger.info(f"Job {job_id} saved to DynamoDB with status 'started'")
        
        # Start Step Functions with the job_id already generated
        state_machine_arn = os.environ.get('STATE_MACHINE_ARN')
        if not state_machine_arn:
            raise ValueError("STATE_MACHINE_ARN environment variable not set")
        
        workflow_input = {
            "job_id": job_id,
            "timestamp": timestamp,
            "session_id": session_id,
            "user_id": user_id,
            "document_s3_key": document_s3_key,
            "bucket_type": bucket_type,
            "terms_profile": terms_profile
        }
        
        # Start the Step Functions execution
        execution_response = sfn_client.start_execution(
            stateMachineArn=state_machine_arn,
            name=job_id.replace('_', '-'),  # Execution name must be unique and valid
            input=json.dumps(workflow_input)
        )
        
        logger.info(f"Started Step Functions execution: {execution_response['executionArn']}")
        
        # Return job_id immediately - frontend can poll using this
        return {
            "statusCode": 200,
            "headers": {
                "Content-Type": "application/json",
                "Access-Control-Allow-Origin": "*"
            },
            "body": json.dumps({
                "job_id": job_id,
                "status": "started",
                "message": "Document review workflow started",
                "execution_arn": execution_response['executionArn'],
                "processing": True
            })
        }
        
    except Exception as e:
        logger.error(f"Error starting workflow: {e}")
        return {
            "statusCode": 500,
            "headers": {
                "Content-Type": "application/json",
                "Access-Control-Allow-Origin": "*"
            },
            "body": json.dumps({
                "error": str(e),
                "status": "failed"
            })
        }

