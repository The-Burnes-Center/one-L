"""
Lambda function for uploading files to S3.
"""

import json
import boto3
import base64
import uuid
import os
from typing import Dict, Any
import logging

logger = logging.getLogger()
logger.setLevel(logging.INFO)

s3_client = boto3.client('s3')

def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Lambda function to upload files to S3.
    
    Expected event format:
    {
        "bucket_type": "knowledge" | "user_documents",
        "files": [
            {
                "filename": "example.txt",
                "content": "base64-encoded-content",
                "content_type": "text/plain"
            }
        ],
        "prefix": "optional/path/prefix/"
    }
    """
    
    try:
        logger.info(f"Upload request received: {json.dumps(event, default=str)}")
        
        # Extract parameters
        bucket_type = event.get('bucket_type', 'user_documents')
        files = event.get('files', [])
        prefix = event.get('prefix', '')
        
        # Get bucket name from environment
        bucket_name = get_bucket_name(bucket_type)
        
        # Validate parameters
        if not files:
            return create_error_response(400, "files array is required")
        
        # Process uploads
        upload_results = []
        for file_data in files:
            result = upload_file_to_s3(file_data, bucket_name, prefix)
            upload_results.append(result)
        
        failed_uploads = [r for r in upload_results if not r['success']]
        
        if failed_uploads:
            return create_error_response(500, "Some files failed to upload", {
                "upload_results": upload_results,
                "failed_count": len(failed_uploads)
            })
        
        return create_success_response({
            "message": "All files uploaded successfully",
            "upload_results": upload_results,
            "uploaded_count": len(upload_results)
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
    else:
        raise ValueError(f"Invalid bucket_type: {bucket_type}")


def upload_file_to_s3(file_data: Dict[str, Any], bucket_name: str, prefix: str) -> Dict[str, Any]:
    """Upload a single file to S3."""
    try:
        filename = file_data.get('filename')
        content = file_data.get('content')
        content_type = file_data.get('content_type', 'application/octet-stream')
        
        if not filename or not content:
            return {
                'success': False,
                'filename': filename,
                'error': 'filename and content are required'
            }
        
        # Generate unique filename
        unique_filename = f"{uuid.uuid4().hex}_{filename}"
        s3_key = f"{prefix.rstrip('/')}/{unique_filename}" if prefix else unique_filename
        
        # Decode content
        file_content = base64.b64decode(content)
        
        # Upload to S3
        s3_client.put_object(
            Bucket=bucket_name,
            Key=s3_key,
            Body=file_content,
            ContentType=content_type,
            Metadata={
                'original_filename': filename,
                'upload_timestamp': str(uuid.uuid4())
            }
        )
        
        logger.info(f"Successfully uploaded {filename} to {bucket_name}/{s3_key}")
        
        return {
            'success': True,
            'filename': filename,
            'unique_filename': unique_filename,
            's3_key': s3_key,
            'bucket_name': bucket_name,
            'content_type': content_type
        }
        
    except Exception as e:
        logger.error(f"Error uploading file {filename}: {str(e)}")
        return {
            'success': False,
            'filename': filename,
            'error': str(e)
        }


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