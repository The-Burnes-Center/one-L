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
        logger.info(f"handle_error received event: {json.dumps(event, default=str)}")
        
        job_id = event.get('job_id')
        timestamp = event.get('timestamp')
        
        # Extract error info - handle both dict and string formats
        error_obj = event.get('error', {})
        if isinstance(error_obj, dict):
            # Step Functions error format: {"Error": "...", "Cause": "..."}
            error_type = error_obj.get('Error', 'UnknownError')
            cause = error_obj.get('Cause', '')
            # Try to parse the Cause as JSON to get the actual error message
            if isinstance(cause, str):
                try:
                    cause_obj = json.loads(cause)
                    error_message = cause_obj.get('errorMessage', str(cause))
                except:
                    error_message = cause
            else:
                error_message = str(cause)
        else:
            error_message = str(error_obj) if error_obj else 'Unknown error'
            error_type = event.get('error_type', 'UnknownError')
        
        # Update job status in DynamoDB
        table_name = os.environ.get('ANALYSES_TABLE_NAME')
        timestamp = event.get('timestamp')  # Get timestamp from workflow context
        
        if table_name and job_id:
            try:
                table = dynamodb.Table(table_name)
                
                # If we don't have timestamp, try to query for the item first
                if not timestamp:
                    try:
                        response = table.query(
                            KeyConditionExpression='analysis_id = :aid',
                            ExpressionAttributeValues={':aid': job_id},
                            Limit=1
                        )
                        if response.get('Items'):
                            timestamp = response['Items'][0].get('timestamp')
                    except Exception as query_error:
                        logger.warning(f"Could not query for timestamp: {query_error}")
                
                if timestamp:
                    table.update_item(
                        Key={'analysis_id': job_id, 'timestamp': timestamp},
                        UpdateExpression='SET #status = :status, stage = :stage, error_message = :error, updated_at = :updated, progress = :progress',
                        ExpressionAttributeNames={'#status': 'status'},
                        ExpressionAttributeValues={
                            ':status': 'failed',
                            ':stage': 'failed',
                            ':error': error_message,
                            ':updated': datetime.utcnow().isoformat(),
                            ':progress': 0
                        }
                    )
                    logger.info(f"Job {job_id} status updated to failed: {error_message}")
                else:
                    logger.warning(f"Could not update job {job_id}: timestamp not found")
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

