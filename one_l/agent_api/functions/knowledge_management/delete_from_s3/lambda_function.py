"""
Lambda function for deleting files from S3.
"""

import json
import boto3
import os
from typing import Dict, Any, List
import logging

logger = logging.getLogger()
logger.setLevel(logging.INFO)

s3_client = boto3.client('s3')

def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Lambda function to delete files from S3.
    
    Expected event format from API Gateway:
    {
        "body": "{\"bucket_type\": \"user_documents\", \"s3_keys\": [\"...\"]}"
    }
    
    Expected direct invocation format:
    {
        "bucket_type": "knowledge" | "user_documents",
        "s3_keys": ["path/to/file1.txt", "path/to/file2.txt"]
    }
    """
    
    try:
        logger.info(f"Delete request received: {json.dumps(event, default=str)}")
        
        # Parse request body - handle both API Gateway proxy and direct invocation
        if 'body' in event and event['body']:
            # API Gateway proxy integration format
            try:
                body_data = json.loads(event['body'])
            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse request body: {e}")
                return create_error_response(400, "Invalid JSON in request body")
        else:
            # Direct invocation format
            body_data = event
        
        # Extract parameters from parsed body
        bucket_type = body_data.get('bucket_type', 'user_documents')
        s3_keys = body_data.get('s3_keys', [])
        
        logger.info(f"Parsed parameters - bucket_type: {bucket_type}, s3_keys count: {len(s3_keys)}")
        
        # Get bucket name from environment
        bucket_name = get_bucket_name(bucket_type)
        
        # Validate parameters
        if not s3_keys:
            return create_error_response(400, "s3_keys array is required")
        
        # Process deletions
        deletion_results = []
        for s3_key in s3_keys:
            result = delete_file_from_s3(bucket_name, s3_key)
            deletion_results.append(result)
        
        failed_deletions = [r for r in deletion_results if not r['success']]
        
        if failed_deletions:
            return create_error_response(500, "Some files failed to delete", {
                "deletion_results": deletion_results,
                "failed_count": len(failed_deletions)
            })
        
        return create_success_response({
            "message": "All files deleted successfully",
            "deletion_results": deletion_results,
            "deleted_count": len(deletion_results)
        })
        
    except Exception as e:
        logger.error(f"Error in delete_from_s3 function: {str(e)}")
        return create_error_response(500, f"Internal server error: {str(e)}")


def get_bucket_name(bucket_type: str) -> str:
    """Get the appropriate bucket name based on type."""
    if bucket_type == "knowledge":
        return os.environ.get("KNOWLEDGE_BUCKET")
    elif bucket_type == "user_documents":
        return os.environ.get("USER_DOCUMENTS_BUCKET")
    elif bucket_type == "agent_processing":
        return os.environ.get("AGENT_PROCESSING_BUCKET")
    else:
        raise ValueError(f"Invalid bucket_type: {bucket_type}")


def delete_file_from_s3(bucket_name: str, s3_key: str) -> Dict[str, Any]:
    """Delete a single file from S3."""
    try:
        # Check if file exists before deletion
        if not file_exists(bucket_name, s3_key):
            return {
                'success': False,
                's3_key': s3_key,
                'error': 'File not found'
            }
        
        # Delete the file
        s3_client.delete_object(Bucket=bucket_name, Key=s3_key)
        
        logger.info(f"Successfully deleted {s3_key} from {bucket_name}")
        
        return {
            'success': True,
            's3_key': s3_key,
            'bucket_name': bucket_name,
            'message': 'File deleted successfully'
        }
        
    except Exception as e:
        logger.error(f"Error deleting file {s3_key}: {str(e)}")
        return {
            'success': False,
            's3_key': s3_key,
            'error': str(e)
        }


def file_exists(bucket_name: str, s3_key: str) -> bool:
    """Check if file exists in S3."""
    try:
        s3_client.head_object(Bucket=bucket_name, Key=s3_key)
        return True
    except Exception:
        return False


def create_success_response(data: Dict[str, Any]) -> Dict[str, Any]:
    """Create successful response."""
    return {
        'statusCode': 200,
        'headers': {
            'Content-Type': 'application/json',
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Methods': 'GET, POST, PUT, DELETE, OPTIONS',
            'Access-Control-Allow-Headers': 'Content-Type, X-Amz-Date, Authorization, X-Api-Key, X-Amz-Security-Token'
        },
        'body': json.dumps(data)
    }


def create_error_response(status_code: int, message: str, data: Dict[str, Any] = None) -> Dict[str, Any]:
    """Create error response."""
    error_body = {'error': message, 'status_code': status_code}
    if data:
        error_body.update(data)
    
    return {
        'statusCode': status_code,
        'headers': {
            'Content-Type': 'application/json',
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Methods': 'GET, POST, PUT, DELETE, OPTIONS',
            'Access-Control-Allow-Headers': 'Content-Type, X-Amz-Date, Authorization, X-Api-Key, X-Amz-Security-Token'
        },
        'body': json.dumps(error_body)
    }