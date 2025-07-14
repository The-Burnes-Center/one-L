"""
Lambda function for retrieving files from S3.
"""

import json
import boto3
import base64
import os
from typing import Dict, Any
import logging

logger = logging.getLogger()
logger.setLevel(logging.INFO)

s3_client = boto3.client('s3')

def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Lambda function to retrieve files from S3.
    
    Expected event format:
    {
        "bucket_type": "knowledge" | "user_documents",
        "s3_key": "path/to/file.txt",
        "return_content": true  # Optional: whether to return file content
    }
    """
    
    try:
        logger.info(f"Retrieve request received: {json.dumps(event, default=str)}")
        
        # Extract parameters
        bucket_type = event.get('bucket_type', 'user_documents')
        s3_key = event.get('s3_key')
        return_content = event.get('return_content', False)
        
        # Get bucket name from environment
        bucket_name = get_bucket_name(bucket_type)
        
        # Validate parameters
        if not s3_key:
            return create_error_response(400, "s3_key is required")
        
        # Check if file exists
        if not file_exists(bucket_name, s3_key):
            return create_error_response(404, f"File not found: {s3_key}")
        
        # Get file metadata
        response = s3_client.head_object(Bucket=bucket_name, Key=s3_key)
        
        result = {
            'success': True,
            's3_key': s3_key,
            'bucket_name': bucket_name,
            'content_type': response.get('ContentType', 'application/octet-stream'),
            'size': response.get('ContentLength', 0),
            'last_modified': response.get('LastModified').isoformat() if response.get('LastModified') else None,
            'metadata': response.get('Metadata', {})
        }
        
        # Include file content if requested
        if return_content:
            file_content = get_file_content(bucket_name, s3_key)
            result['content'] = file_content
        
        logger.info(f"Successfully retrieved metadata for {s3_key}")
        
        return create_success_response(result)
        
    except Exception as e:
        logger.error(f"Error in retrieve_from_s3 function: {str(e)}")
        return create_error_response(500, f"Internal server error: {str(e)}")


def get_bucket_name(bucket_type: str) -> str:
    """Get the appropriate bucket name based on type."""
    if bucket_type == "knowledge":
        return os.environ.get("KNOWLEDGE_BUCKET")
    elif bucket_type == "user_documents":
        return os.environ.get("USER_DOCUMENTS_BUCKET")
    else:
        raise ValueError(f"Invalid bucket_type: {bucket_type}")


def file_exists(bucket_name: str, s3_key: str) -> bool:
    """Check if file exists in S3."""
    try:
        s3_client.head_object(Bucket=bucket_name, Key=s3_key)
        return True
    except Exception:
        return False


def get_file_content(bucket_name: str, s3_key: str) -> str:
    """Get file content from S3 and encode as base64."""
    try:
        response = s3_client.get_object(Bucket=bucket_name, Key=s3_key)
        content = response['Body'].read()
        return base64.b64encode(content).decode('utf-8')
    except Exception as e:
        logger.error(f"Error reading file content: {str(e)}")
        raise


def create_success_response(data: Dict[str, Any]) -> Dict[str, Any]:
    """Create successful response."""
    return {
        'statusCode': 200,
        'body': json.dumps(data)
    }


def create_error_response(status_code: int, message: str, data: Dict[str, Any] = None) -> Dict[str, Any]:
    """Create error response."""
    error_body = {'error': message, 'status_code': status_code}
    if data:
        error_body.update(data)
    
    return {
        'statusCode': status_code,
        'body': json.dumps(error_body)
    }