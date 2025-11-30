"""
Generate redline Lambda function.
Wraps existing redline_document function, uses character positions from conflicts.
"""

import json
import boto3
import logging
import os
from agent_api.agent.prompts.models import RedlineOutput
from agent_api.agent.tools import redline_document

logger = logging.getLogger()
logger.setLevel(logging.INFO)

def lambda_handler(event, context):
    """
    Generate redlined document from conflicts.
    
    Args:
        event: Lambda event with conflicts_json, document_s3_key, bucket_name, session_id, user_id
        context: Lambda context
        
    Returns:
        RedlineOutput with success, redlined_document_s3_key, error
    """
    try:
        conflicts_json = event.get('conflicts_json')
        document_s3_key = event.get('document_s3_key')
        bucket_name = event.get('bucket_name')
        session_id = event.get('session_id')
        user_id = event.get('user_id')
        
        if not conflicts_json or not document_s3_key:
            raise ValueError("conflicts_json and document_s3_key are required")
        
        # Parse conflicts
        if isinstance(conflicts_json, str):
            conflicts_data = json.loads(conflicts_json)
        else:
            conflicts_data = conflicts_json
        
        # Extract conflicts list
        if isinstance(conflicts_data, dict) and 'conflicts' in conflicts_data:
            conflicts_list = conflicts_data['conflicts']
        elif isinstance(conflicts_data, list):
            conflicts_list = conflicts_data
        else:
            raise ValueError("Invalid conflicts format")
        
        # Convert to format expected by redline_document
        # redline_document expects analysis JSON string
        analysis_json = json.dumps({
            "explanation": conflicts_data.get('explanation', '') if isinstance(conflicts_data, dict) else '',
            "conflicts": conflicts_list
        })
        
        # Call redline_document
        logger.info(f"Generating redline for {len(conflicts_list)} conflicts")
        result = redline_document(
            analysis=analysis_json,
            document_s3_key=document_s3_key,
            bucket_type="user_documents",
            session_id=session_id,
            user_id=user_id
        )
        
        # Extract result
        if result.get('success'):
            redlined_s3_key = result.get('redlined_document_s3_key', '')
            output = RedlineOutput(
                success=True,
                redlined_document_s3_key=redlined_s3_key,
                error=None
            )
        else:
            error_msg = result.get('error', 'Unknown error')
            output = RedlineOutput(
                success=False,
                redlined_document_s3_key=None,
                error=error_msg
            )
        
        return {
            "statusCode": 200,
            "body": output.model_dump_json()
        }
        
    except Exception as e:
        logger.error(f"Error in generate_redline: {e}")
        output = RedlineOutput(
            success=False,
            redlined_document_s3_key=None,
            error=str(e)
        )
        return {
            "statusCode": 200,
            "body": output.model_dump_json()
        }

