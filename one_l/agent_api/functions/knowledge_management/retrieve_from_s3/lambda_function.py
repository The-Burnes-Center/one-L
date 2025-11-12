"""
Lambda function for retrieving files from S3.
"""

import json
import boto3
import base64
import os
from typing import Dict, Any, Optional
import logging

logger = logging.getLogger()
logger.setLevel(logging.INFO)

s3_client = boto3.client('s3')

def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Lambda function to retrieve files from S3.
    
    Expected event format from API Gateway:
    {
        "body": "{\"bucket_type\": \"user_documents\", \"s3_key\": \"...\", \"return_content\": true}"
    }
    
    Expected direct invocation format:
    {
        "bucket_type": "knowledge" | "user_documents",
        "s3_key": "path/to/file.txt",
        "return_content": true  # Optional: whether to return file content
    }
    """
    
    try:
        logger.info(f"Retrieve request received: {json.dumps(event, default=str)}")
        
        body_data, query_params, http_method = parse_event(event)
        action = (body_data.get('action') or query_params.get('action', '')).lower()
        bucket_type = body_data.get('bucket_type') or query_params.get('bucket_type', 'user_documents')
        return_content = body_data.get('return_content', False)
        s3_key = body_data.get('s3_key') or query_params.get('s3_key')
        prefix = body_data.get('prefix') or query_params.get('prefix')
        max_keys = body_data.get('max_keys') or query_params.get('max_keys')
        continuation_token = body_data.get('continuation_token') or query_params.get('continuation_token')
        
        logger.info(
            "Parsed parameters - action: %s, bucket_type: %s, s3_key: %s, prefix: %s, max_keys: %s, continuation_token: %s, return_content: %s",
            action or 'retrieve',
            bucket_type,
            s3_key,
            prefix,
            max_keys,
            continuation_token,
            return_content,
        )
        
        # Get bucket name from environment
        bucket_name = get_bucket_name(bucket_type)
        
        # Handle list action
        if action == 'list':
            max_keys_int = ensure_int(max_keys, default=100, minimum=1, maximum=1000)
            list_result = list_files(bucket_name, prefix=prefix, max_keys=max_keys_int, continuation_token=continuation_token)
            return create_success_response(list_result)
        
        # Validate parameters for retrieve action
        if not s3_key:
            return create_error_response(400, "s3_key is required for retrieve action")
        
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
        
    except ValueError as ve:
        logger.error(f"Validation error in retrieve_from_s3 function: {str(ve)}")
        return create_error_response(400, str(ve))
    except Exception as e:
        logger.error(f"Error in retrieve_from_s3 function: {str(e)}", exc_info=True)
        return create_error_response(500, f"Internal server error: {str(e)}")


def parse_event(event: Dict[str, Any]) -> (Dict[str, Any], Dict[str, Any], Optional[str]):
    """
    Parse the incoming event and return body data, query parameters, and HTTP method (if any).
    Supports both API Gateway proxy events and direct Lambda invocations.
    """
    http_method = event.get('httpMethod')
    query_params = event.get('queryStringParameters') or {}
    body_data: Dict[str, Any] = {}
    
    if http_method:
        logger.info(f"HTTP method detected: {http_method}")
        if http_method in ('GET', 'DELETE'):
            # GET and DELETE requests typically send data via query parameters
            body_data = {}
        elif event.get('body'):
            try:
                body_data = json.loads(event['body'])
            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse request body: {e}")
                raise ValueError("Invalid JSON in request body")
    else:
        # Direct invocation - use event as body
        body_data = event if isinstance(event, dict) else {}
    
    return body_data, query_params, http_method


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


def list_files(bucket_name: str, prefix: Optional[str], max_keys: int, continuation_token: Optional[str]) -> Dict[str, Any]:
    """List files in the specified S3 bucket."""
    try:
        list_kwargs: Dict[str, Any] = {
            'Bucket': bucket_name,
            'MaxKeys': max_keys
        }
        if prefix:
            list_kwargs['Prefix'] = prefix
        if continuation_token:
            list_kwargs['ContinuationToken'] = continuation_token
        
        response = s3_client.list_objects_v2(**list_kwargs)
        contents = response.get('Contents', [])
        files = [
            {
                's3_key': item.get('Key'),
                'size': item.get('Size'),
                'last_modified': item.get('LastModified').isoformat() if item.get('LastModified') else None,
                'storage_class': item.get('StorageClass')
            }
            for item in contents
            if item.get('Key') and not item.get('Key').endswith('/')  # Exclude directory placeholders
        ]
        
        logger.info("Listed %d objects for prefix '%s' in bucket '%s'", len(files), prefix, bucket_name)
        
        return {
            'success': True,
            'bucket_name': bucket_name,
            'prefix': prefix,
            'files': files,
            'key_count': response.get('KeyCount', len(files)),
            'next_continuation_token': response.get('NextContinuationToken'),
            'is_truncated': response.get('IsTruncated', False),
            'max_keys': max_keys
        }
    except Exception as e:
        logger.error(f"Error listing files: {e}", exc_info=True)
        raise


def ensure_int(value: Optional[str], default: int, minimum: int, maximum: int) -> int:
    """Ensure a numeric string parses to an int within bounds."""
    if value is None:
        return default
    try:
        int_value = int(value)
    except (TypeError, ValueError):
        logger.warning(f"Invalid max_keys value '{value}', falling back to default {default}")
        return default
    
    if int_value < minimum:
        return minimum
    if int_value > maximum:
        return maximum
    return int_value


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