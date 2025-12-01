"""
Store large results in S3 Lambda function.
Used to store KB query results that exceed Step Functions payload limits.
"""

import json
import boto3
import logging
import os
from datetime import datetime

logger = logging.getLogger()
logger.setLevel(logging.INFO)

s3_client = boto3.client('s3')

def lambda_handler(event, context):
    """
    Store large results (KB query results) in S3 and return S3 key.
    
    Args:
        event: Lambda event with kb_results (list), job_id, session_id, bucket_name
        context: Lambda context
        
    Returns:
        Dict with s3_key pointing to stored results
    """
    try:
        # Handle both KB results and chunk analyses (reuse same function)
        kb_results = event.get('kb_results', [])
        storage_type = event.get('storage_type', 'kb_results')  # 'kb_results' or 'chunk_analyses'
        job_id = event.get('job_id')
        session_id = event.get('session_id')
        bucket_name = event.get('bucket_name') or os.environ.get('AGENT_PROCESSING_BUCKET')
        
        if not bucket_name:
            raise ValueError("bucket_name is required")
        
        if not kb_results:
            # No results to store, return empty
            return {'s3_key': None, 'has_results': False}
        
        # Determine S3 key based on storage type
        if storage_type == 'chunk_analyses':
            s3_key = f"{session_id}/chunk_analyses/{job_id}_chunk_analyses.json"
            storage_name = "chunk analyses"
        else:
            s3_key = f"{session_id}/kb_results/{job_id}_kb_results.json"
            storage_name = "KB results"
        
        results_json = json.dumps(kb_results)
        results_size = len(results_json.encode('utf-8'))
        
        # Always store in S3 for consistency and easier cleanup
        s3_client.put_object(
            Bucket=bucket_name,
            Key=s3_key,
            Body=results_json.encode('utf-8'),
            ContentType='application/json'
        )
        
        logger.info(f"Stored {len(kb_results)} {storage_name} ({results_size} bytes) in S3: {s3_key}")
        
        return {
            's3_key': s3_key,
            'has_results': True,
            'result_count': len(kb_results),
            'size_bytes': results_size
        }
        
    except Exception as e:
        logger.error(f"Error storing large results in S3: {e}")
        raise

