"""
Handle error Lambda function.
Updates job status to failed in DynamoDB.
"""

import json
import boto3
import logging
import os
from datetime import datetime
from agent_api.agent.prompts.models import ErrorOutput

logger = logging.getLogger()
logger.setLevel(logging.INFO)

dynamodb = boto3.resource('dynamodb')

def lambda_handler(event, context):
    """
    Handle errors and update job status.
    
    Args:
        event: Lambda event with job_id, error, error_type, Cause (from Step Functions)
        context: Lambda context
        
    Returns:
        ErrorOutput with error, error_type, timestamp
    """
    try:
        job_id = event.get('job_id') or event.get('Error', {}).get('Cause', {}).get('job_id')
        error_message = event.get('error') or event.get('Error', {}).get('Error', 'Unknown error')
        error_type = event.get('error_type') or event.get('Error', {}).get('Type', 'LambdaError')
        
        # Update job status in DynamoDB
        table_name = os.environ.get('ANALYSES_TABLE_NAME')
        if table_name and job_id:
            try:
                table = dynamodb.Table(table_name)
                table.update_item(
                    Key={'analysis_id': job_id},
                    UpdateExpression='SET #status = :status, error_message = :error, updated_at = :timestamp',
                    ExpressionAttributeNames={'#status': 'status'},
                    ExpressionAttributeValues={
                        ':status': 'failed',
                        ':error': error_message,
                        ':timestamp': datetime.utcnow().isoformat()
                    }
                )
                logger.info(f"Job {job_id} status updated to failed")
            except Exception as db_error:
                logger.warning(f"Could not update DynamoDB: {db_error}")
        
        # Create validated output
        output = ErrorOutput(
            error=error_message,
            error_type=error_type,
            timestamp=datetime.utcnow().isoformat()
        )
        
        return {
            "statusCode": 200,
            "body": output.model_dump_json()
        }
        
    except Exception as e:
        logger.error(f"Error in handle_error: {e}")
        # Return basic error output
        output = ErrorOutput(
            error=str(e),
            error_type="ErrorHandlerError",
            timestamp=datetime.utcnow().isoformat()
        )
        return {
            "statusCode": 200,
            "body": output.model_dump_json()
        }

