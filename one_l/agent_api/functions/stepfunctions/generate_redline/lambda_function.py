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

s3_client = boto3.client('s3')

# Import progress tracker
try:
    from shared.progress_tracker import update_progress
except ImportError:
    update_progress = None

def lambda_handler(event, context):
    """
    Generate redlined document from conflicts.
    
    Args:
        event: Lambda event with conflicts_result (from analyze step), document_s3_key, session_id, user_id
        context: Lambda context
        
    Returns:
        RedlineOutput with success, redlined_document_s3_key, error
    """
    try:
        # Get workflow context
        document_s3_key = event.get('document_s3_key')
        session_id = event.get('session_id')
        user_id = event.get('user_id')
        
        # CRITICAL: Load conflicts from S3 (merge_chunk_results stores in S3)
        # conflicts_result may contain conflicts_s3_key (S3 reference) or inline data (legacy)
        conflicts_result = event.get('conflicts_result', {})
        conflicts_s3_key = event.get('conflicts_s3_key') or (conflicts_result.get('conflicts_s3_key') if isinstance(conflicts_result, dict) else None)
        bucket_name = event.get('bucket_name') or os.environ.get('AGENT_PROCESSING_BUCKET')
        
        logger.info(f"generate_redline received event keys: {list(event.keys())}")
        
        if not document_s3_key:
            raise ValueError(f"document_s3_key is required")
        
        # Load conflicts from S3 if S3 key provided
        if conflicts_s3_key and bucket_name:
            try:
                conflicts_response = s3_client.get_object(Bucket=bucket_name, Key=conflicts_s3_key)
                conflicts_json = conflicts_response['Body'].read().decode('utf-8')
                conflicts_data = json.loads(conflicts_json)
                logger.info(f"Loaded conflicts from S3: {conflicts_s3_key}")
            except Exception as e:
                logger.error(f"CRITICAL: Failed to load conflicts from S3 {conflicts_s3_key}: {e}")
                raise  # Fail fast - conflicts must be in S3
        else:
            # Fallback: try to parse from conflicts_result (legacy support)
            conflicts_json = event.get('conflicts_json') or conflicts_result
            if not conflicts_json:
                raise ValueError(f"conflicts_s3_key or conflicts_json is required")
            
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
        
        # Get bucket_type from event (defaults to user_documents for backward compatibility)
        bucket_type = event.get('bucket_type', 'user_documents')
        
        # Call redline_document
        # CRITICAL: Function signature expects 'analysis_data', not 'analysis'
        logger.info(f"Generating redline for {len(conflicts_list)} conflicts, bucket_type={bucket_type}")
        result = redline_document(
            analysis_data=analysis_json,  # Fixed: was 'analysis=', should be 'analysis_data='
            document_s3_key=document_s3_key,
            bucket_type=bucket_type,  # Use bucket_type from event, not hardcoded
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
            
            # Update progress
            job_id = event.get('job_id')
            timestamp = event.get('timestamp')
            if update_progress and job_id and timestamp:
                update_progress(
                    job_id, timestamp, 'generating_redlines',
                    f'Generated redlined document with {len(conflicts_list)} conflicts...'
                )
            
            # Return plain result (Step Functions merges via result_path)
            return output.model_dump()
        else:
            # CRITICAL: Raise exception when redline fails so Step Functions treats it as a failure
            # This ensures the execution status is FAILED, not SUCCEEDED
            error_msg = result.get('error', 'Unknown error')
            logger.error(f"Redline generation failed: {error_msg}")
            raise Exception(f"Failed to generate redlined document: {error_msg}")
        
    except Exception as e:
        logger.error(f"Error in generate_redline: {e}")
        output = RedlineOutput(
            success=False,
            redlined_document_s3_key=None,
            error=str(e)
        )
        return output.model_dump()

