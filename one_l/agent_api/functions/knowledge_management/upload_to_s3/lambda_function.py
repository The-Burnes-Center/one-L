"""
Lambda function for generating presigned URLs for direct S3 upload.
This approach is more efficient than uploading through Lambda as it:
- Supports larger files (not limited by Lambda memory/payload size)
- Reduces Lambda execution time and costs
- Provides faster uploads by going directly to S3
"""

import json
import boto3
import uuid
import os
from typing import Dict, Any
import logging

logger = logging.getLogger()
logger.setLevel(logging.INFO)

s3_client = boto3.client('s3')

def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Lambda function to generate presigned URLs for direct S3 upload.
    
    Expected event format from API Gateway:
    {
        "body": "{\"bucket_type\": \"user_documents\", \"files\": [...], \"prefix\": \"...\", \"session_id\": \"...\", \"user_id\": \"...\"}"
    }
    
    Expected direct invocation format:
    {
        "bucket_type": "knowledge" | "user_documents",
        "files": [
            {
                "filename": "example.txt",
                "content_type": "text/plain",
                "file_size": 1024
            }
        ],
        "prefix": "optional/path/prefix/",
        "session_id": "optional session UUID for session-based storage",
        "user_id": "optional user UUID for session-based storage"
    }
    """
    
    try:
        logger.info(f"Presigned URL request received: {json.dumps(event, default=str)}")
        
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
        files = body_data.get('files', [])
        prefix = body_data.get('prefix', '')
        
        # NEW: Extract session context for session-based storage
        session_id = body_data.get('session_id')
        user_id = body_data.get('user_id')
        
        logger.info(f"Parsed parameters - bucket_type: {bucket_type}, files count: {len(files)}, prefix: {prefix}, session_id: {session_id}, user_id: {user_id}")
        
        # Get bucket name from environment
        bucket_name = get_bucket_name(bucket_type)
        
        # NEW: Generate session-based prefix for reference documents
        if session_id and user_id and bucket_type == 'user_documents':
            # Check if this is a reference document upload
            if 'reference-docs' in prefix or prefix == '':
                # Generate session-based prefix
                session_prefix = f"sessions/{user_id}/{session_id}/reference-docs"
                logger.info(f"Converting to session-based storage: {prefix} → {session_prefix}")
                prefix = session_prefix
            else:
                # For other document types, keep existing prefix but add session context
                logger.info(f"Session context available but keeping existing prefix for non-reference docs: {prefix}")
        
        # Validate parameters
        if not files:
            return create_error_response(400, "files array is required")
        
        # Generate presigned URLs
        presigned_urls = []
        for file_data in files:
            result = generate_presigned_url(file_data, bucket_name, prefix, session_id, user_id)
            presigned_urls.append(result)
        
        failed_generations = [r for r in presigned_urls if not r['success']]
        
        if failed_generations:
            return create_error_response(500, "Some presigned URLs failed to generate", {
                "presigned_urls": presigned_urls,
                "failed_count": len(failed_generations)
            })
        
        return create_success_response({
            "message": "Presigned URLs generated successfully",
            "presigned_urls": presigned_urls,
            "generated_count": len(presigned_urls)
        })
        
    except Exception as e:
        logger.error(f"Error in upload_to_s3 function: {str(e)}")
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


def generate_presigned_url(file_data: Dict[str, Any], bucket_name: str, prefix: str, session_id: str = None, user_id: str = None) -> Dict[str, Any]:
    """Generate a presigned URL for direct S3 upload."""
    try:
        filename = file_data.get('filename')
        content_type = file_data.get('content_type', 'application/octet-stream')
        file_size = file_data.get('file_size', 0)
        
        if not filename:
            return {
                'success': False,
                'filename': filename,
                'error': 'filename is required'
            }
        
        # Validate file size (10MB limit)
        max_file_size = 10 * 1024 * 1024  # 10MB
        if file_size > max_file_size:
            return {
                'success': False,
                'filename': filename,
                'error': f'File size ({file_size} bytes) exceeds maximum allowed size ({max_file_size} bytes)'
            }
        
        # Generate unique filename
        unique_filename = f"{uuid.uuid4().hex}_{filename}"
        s3_key = f"{prefix.rstrip('/')}/{unique_filename}" if prefix else unique_filename
        
        # Generate presigned URL for PUT operation
        presigned_url = s3_client.generate_presigned_url(
            'put_object',
            Params={
                'Bucket': bucket_name,
                'Key': s3_key,
                'ContentType': content_type,
                'Metadata': {
                    'original_filename': filename,
                    'upload_timestamp': str(uuid.uuid4()),
                    'session_id': session_id or 'no-session',
                    'user_id': user_id or 'unknown'
                }
            },
            ExpiresIn=3600  # URL expires in 1 hour
        )
        
        logger.info(f"Generated presigned URL for {filename} -> {bucket_name}/{s3_key}")
        
        return {
            'success': True,
            'filename': filename,
            'unique_filename': unique_filename,
            's3_key': s3_key,
            'bucket_name': bucket_name,
            'content_type': content_type,
            'presigned_url': presigned_url,
            'expires_in': 3600,
            'session_id': session_id,
            'user_id': user_id,
            'is_session_based': bool(session_id and user_id)
        }
        
    except Exception as e:
        logger.error(f"Error generating presigned URL for {filename}: {str(e)}")
        return {
            'success': False,
            'filename': filename,
            'error': str(e)
        }


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