"""
Save results Lambda function.
Wraps existing save_analysis_to_dynamodb function.
"""

import json
import boto3
import logging
from agent_api.agent.prompts.models import SaveResultsOutput
from agent_api.agent.tools import save_analysis_to_dynamodb

logger = logging.getLogger()
logger.setLevel(logging.INFO)

def lambda_handler(event, context):
    """
    Save analysis results to DynamoDB.
    
    Args:
        event: Lambda event with analysis_json, session_id, user_id, document_s3_key, redlined_s3_key
        context: Lambda context
        
    Returns:
        SaveResultsOutput with success, analysis_id, error
    """
    try:
        analysis_json = event.get('analysis_json')
        session_id = event.get('session_id')
        user_id = event.get('user_id')
        document_s3_key = event.get('document_s3_key')
        redlined_s3_key = event.get('redlined_s3_key')
        
        if not analysis_json or not session_id or not user_id:
            raise ValueError("analysis_json, session_id, and user_id are required")
        
        # Convert to string if needed
        if isinstance(analysis_json, dict):
            analysis_json = json.dumps(analysis_json)
        
        # Call save_analysis_to_dynamodb
        logger.info(f"Saving analysis results for session {session_id}")
        result = save_analysis_to_dynamodb(
            analysis=analysis_json,
            document_s3_key=document_s3_key,
            redlined_document_s3_key=redlined_s3_key,
            session_id=session_id,
            user_id=user_id
        )
        
        # Extract analysis_id from result
        analysis_id = result.get('analysis_id', session_id)
        
        output = SaveResultsOutput(
            success=True,
            analysis_id=analysis_id,
            error=None
        )
        
        logger.info(f"Analysis results saved: {analysis_id}")
        
        return {
            "statusCode": 200,
            "body": output.model_dump_json()
        }
        
    except Exception as e:
        logger.error(f"Error in save_results: {e}")
        output = SaveResultsOutput(
            success=False,
            analysis_id=event.get('session_id', ''),
            error=str(e)
        )
        return {
            "statusCode": 200,
            "body": output.model_dump_json()
        }

