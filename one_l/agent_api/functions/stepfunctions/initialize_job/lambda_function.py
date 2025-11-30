"""
Initialize job Lambda function.
Creates job_id and saves initial status to DynamoDB.
"""

import json
import boto3
import logging
import os
from datetime import datetime
from agent_api.agent.prompts.models import JobInitializationOutput

logger = logging.getLogger()
logger.setLevel(logging.INFO)

dynamodb = boto3.resource('dynamodb')

def lambda_handler(event, context):
    """
    Initialize a new job for document review.
    
    Args:
        event: Lambda event with session_id, user_id, document_s3_key
        context: Lambda context
        
    Returns:
        JobInitializationOutput with job_id, status, created_at
    """
    try:
        session_id = event.get('session_id')
        user_id = event.get('user_id')
        document_s3_key = event.get('document_s3_key')
        
        if not session_id or not user_id:
            raise ValueError("session_id and user_id are required")
        
        # Generate job_id
        job_id = f"{session_id}_{int(datetime.now().timestamp() * 1000)}"
        
        # Save initial status to DynamoDB
        table_name = os.environ.get('ANALYSES_TABLE_NAME')
        if table_name:
            table = dynamodb.Table(table_name)
            table.put_item(
                Item={
                    'analysis_id': job_id,
                    'session_id': session_id,
                    'user_id': user_id,
                    'status': 'initialized',
                    'created_at': datetime.utcnow().isoformat(),
                    'document_s3_key': document_s3_key
                }
            )
            logger.info(f"Job {job_id} initialized in DynamoDB")
        
        # Create validated output
        output = JobInitializationOutput(
            job_id=job_id,
            status='initialized',
            created_at=datetime.utcnow().isoformat()
        )
        
        return {
            "statusCode": 200,
            "body": output.model_dump_json()
        }
        
    except Exception as e:
        logger.error(f"Error initializing job: {e}")
        raise

