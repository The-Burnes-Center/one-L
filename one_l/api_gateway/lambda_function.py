import json
import boto3
import base64
import uuid
from typing import Dict, Any
import logging

logger = logging.getLogger()
logger.setLevel(logging.INFO)
s3_client = boto3.client('s3')

def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Lambda function to handle S3 file uploads via API Gateway.
    
    Expected request body format:
    {
        "bucket_name": "your-bucket-name",
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
        # Parse request body
        if event.get('isBase64Encoded', False):
            body = base64.b64decode(event['body']).decode('utf-8')
        else:
            body = event.get('body', '{}')
        
        request_data = json.loads(body)
        
        # Extract parameters
        bucket_name = request_data.get('bucket_name')
        files = request_data.get('files', [])
        prefix = request_data.get('prefix', '')
        
        # Validate parameters
        if not bucket_name:
            return create_error_response(400, "bucket_name is required")
        
        if not files:
            return create_error_response(400, "files array is required")
        
        # Validate bucket exists
        if not bucket_exists(bucket_name):
            return create_error_response(404, f"Bucket '{bucket_name}' does not exist")
        
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
        logger.error(f"Error: {str(e)}")
        return create_error_response(500, f"Internal server error: {str(e)}")


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
        
        unique_filename = f"{uuid.uuid4().hex}_{filename}"
        s3_key = f"{prefix.rstrip('/')}/{unique_filename}" if prefix else unique_filename
        
        file_content = base64.b64decode(content)
        
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
        
        return {
            'success': True,
            'filename': filename,
            'unique_filename': unique_filename,
            's3_key': s3_key,
            's3_url': f"https://{bucket_name}.s3.amazonaws.com/{s3_key}",
            'content_type': content_type
        }
        
    except Exception as e:
        logger.error(f"Error uploading file {filename}: {str(e)}")
        return {
            'success': False,
            'filename': filename,
            'error': str(e)
        }


def bucket_exists(bucket_name: str) -> bool:
    """Check if S3 bucket exists and is accessible."""
    try:
        s3_client.head_bucket(Bucket=bucket_name)
        return True
    except Exception as e:
        logger.error(f"Bucket check failed for {bucket_name}: {str(e)}")
        return False


def create_success_response(data: Dict[str, Any]) -> Dict[str, Any]:
    """Create successful response."""
    return {
        'statusCode': 200,
        'headers': {
            'Content-Type': 'application/json',
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Methods': 'POST, OPTIONS',
            'Access-Control-Allow-Headers': 'Content-Type, X-Amz-Date, Authorization, X-Api-Key'
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
            'Access-Control-Allow-Methods': 'POST, OPTIONS',
            'Access-Control-Allow-Headers': 'Content-Type, X-Amz-Date, Authorization, X-Api-Key'
        },
        'body': json.dumps(error_body)
    }