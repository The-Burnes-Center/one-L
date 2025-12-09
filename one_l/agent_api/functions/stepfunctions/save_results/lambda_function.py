"""
Save results Lambda function.
Wraps existing save_analysis_to_dynamodb function.
"""

import json
import boto3
import logging
import os
from agent_api.agent.prompts.models import SaveResultsOutput
from agent_api.agent.tools import save_analysis_to_dynamodb

logger = logging.getLogger()
logger.setLevel(logging.INFO)

s3_client = boto3.client('s3')

# Import progress tracker
try:
    from shared.progress_tracker import mark_completed
except ImportError:
    mark_completed = None

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
        # CRITICAL: Load conflicts from S3 if conflicts_s3_key provided (merge_chunk_results stores in S3)
        conflicts_s3_key = event.get('conflicts_s3_key')
        analysis_json = event.get('analysis_json')
        bucket_name = event.get('bucket_name') or os.environ.get('AGENT_PROCESSING_BUCKET')
        session_id = event.get('session_id')
        user_id = event.get('user_id')
        document_s3_key = event.get('document_s3_key')
        redlined_s3_key = event.get('redlined_s3_key')
        
        logger.info(f"save_results received event keys: {list(event.keys())}")
        
        if not session_id or not user_id:
            raise ValueError(f"session_id and user_id are required. Got session_id={session_id}, user_id={user_id}")
        
        # Load conflicts from S3 if S3 key provided
        if conflicts_s3_key and bucket_name:
            try:
                conflicts_response = s3_client.get_object(Bucket=bucket_name, Key=conflicts_s3_key)
                conflicts_json = conflicts_response['Body'].read().decode('utf-8')
                analysis_json = json.loads(conflicts_json)
                logger.info(f"Loaded conflicts from S3: {conflicts_s3_key}")
            except Exception as e:
                logger.error(f"CRITICAL: Failed to load conflicts from S3 {conflicts_s3_key}: {e}")
                raise  # Fail fast - conflicts must be in S3
        
        if not analysis_json:
            raise ValueError(f"analysis_json or conflicts_s3_key is required")
        
        # Get required parameters from event
        job_id = event.get('job_id')
        bucket_type = event.get('bucket_type', 'agent_processing')
        
        # Convert conflicts_result (dict) to analysis_data (string) if needed
        analysis_data = analysis_json
        if isinstance(analysis_data, dict):
            # If it's a ConflictDetectionOutput dict, convert to analysis JSON format
            if 'conflicts' in analysis_data:
                analysis_data = json.dumps({
                    "explanation": analysis_data.get('explanation', ''),
                    "conflicts": analysis_data.get('conflicts', [])
                })
            else:
                # Otherwise just stringify it
                analysis_data = json.dumps(analysis_data)
        elif not isinstance(analysis_data, str):
            # Convert other types to string
            analysis_data = json.dumps(analysis_data)
        
        # Prepare redlined_result dict if redlined_s3_key is provided
        redlined_result = None
        if redlined_s3_key:
            redlined_result = {
                'redlined_document': redlined_s3_key
            }
        
        # Use job_id as analysis_id (they're the same in Step Functions workflow)
        analysis_id = job_id or session_id
        
        # CRITICAL: Get timestamp from event to update existing job record (not create duplicate)
        timestamp = event.get('timestamp')
        if not timestamp:
            logger.warning(f"No timestamp provided in event for job {job_id}, will create new record")
        
        # Call save_analysis_to_dynamodb with correct parameters
        logger.info(f"Saving analysis results for session {session_id}, analysis_id: {analysis_id}, timestamp: {timestamp}")
        result = save_analysis_to_dynamodb(
            analysis_id=analysis_id,
            document_s3_key=document_s3_key,
            analysis_data=analysis_data,
            bucket_type=bucket_type,
            usage_data={},  # Step Functions workflow doesn't track usage data
            thinking="",
            citations=None,
            session_id=session_id,
            user_id=user_id,
            redlined_result=redlined_result,
            timestamp=timestamp  # Pass timestamp to update existing job record
        )
        
        # Extract analysis_id from result (should be same as what we passed)
        analysis_id = result.get('analysis_id', analysis_id)
        
        output = SaveResultsOutput(
            success=True,
            analysis_id=analysis_id,
            error=None
        )
        
        logger.info(f"Analysis results saved: {analysis_id}")
        
        # Mark job as completed
        job_id = event.get('job_id')
        timestamp = event.get('timestamp')
        if mark_completed and job_id and timestamp:
            mark_completed(
                job_id, timestamp,
                {
                    'redlined_document_s3_key': redlined_s3_key,
                    'analysis': analysis_json if isinstance(analysis_json, str) else json.dumps(analysis_json),
                    'conflicts_count': len(json.loads(analysis_json if isinstance(analysis_json, str) else json.dumps(analysis_json)).get('conflicts', []))
                },
                session_id=session_id,
                user_id=user_id
            )
        
        # Return plain result
        return output.model_dump()
        
    except Exception as e:
        logger.error(f"Error in save_results: {e}")
        output = SaveResultsOutput(
            success=False,
            analysis_id=event.get('session_id', ''),
            error=str(e)
        )
        return output.model_dump()

