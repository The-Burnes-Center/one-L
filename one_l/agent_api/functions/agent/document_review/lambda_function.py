"""
Lambda function for AI-powered document review.
Handles vendor document analysis for conflicts with reference documents.
"""

import json
import boto3
import os
import logging
import sys
from typing import Dict, Any
from docx import Document
import io
import uuid
from datetime import datetime

# Add the agent module to the path for packaged dependencies
sys.path.insert(0, '/opt/python')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))))

# Import business logic from agent_api - using composition pattern
try:
    from agent_api.agent.agent import Agent
except ImportError:
    # Fallback for local testing
    import sys
    import os
    sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..', '..', '..'))
    from agent_api.agent.agent import Agent

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Initialize S3 client
s3_client = boto3.client('s3')

def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Lambda function to review vendor documents using AI.
    
    Expected event format from API Gateway:
    {
        "body": "{\"document_s3_key\": \"path/to/document.docx\", \"bucket_type\": \"user_documents\"}"
    }
    """
    
    try:
        logger.info(f"Document review request received: {json.dumps(event, default=str)}")
        
        # Handle CORS preflight requests
        if event.get('httpMethod') == 'OPTIONS':
            return {
                'statusCode': 200,
                'headers': {
                    'Access-Control-Allow-Origin': '*',
                    'Access-Control-Allow-Methods': 'GET, POST, PUT, DELETE, OPTIONS',
                    'Access-Control-Allow-Headers': 'Content-Type, X-Amz-Date, Authorization, X-Api-Key, X-Amz-Security-Token',
                    'Access-Control-Allow-Credentials': 'false',
                    'Access-Control-Max-Age': '86400'
                },
                'body': ''
            }
        
        # Parse request body - handle both API Gateway proxy and direct invocation
        if 'body' in event and event['body']:
            try:
                body_data = json.loads(event['body'])
            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse request body: {e}")
                return create_error_response(400, "Invalid JSON in request body")
        else:
            body_data = event
        
        # Extract parameters from parsed body
        document_s3_key = body_data.get('document_s3_key')
        bucket_type = body_data.get('bucket_type', 'user_documents')
        
        logger.info(f"Parsed parameters - document_s3_key: {document_s3_key}, bucket_type: {bucket_type}")
        
        # Validate required parameters
        if not document_s3_key:
            return create_error_response(400, "document_s3_key is required")
        
        # Get agent configuration
        knowledge_base_id = os.environ.get('KNOWLEDGE_BASE_ID')
        region = os.environ.get('REGION')
        
        if not knowledge_base_id:
            return create_error_response(500, "Knowledge base ID not configured")
        
        # Initialize the document review agent (composition pattern)
        agent = Agent(knowledge_base_id, region)
        
        # Extract document content for AI analysis
        document_content = extract_document_content(bucket_type, document_s3_key)
        
        # Run the document review (synchronous operation)
        review_result = agent.review_document(document_content)
        
        if not review_result.get('success', False):
            return create_error_response(500, f"Document review failed: {review_result.get('error', 'Unknown error')}")
        
        # Create redlined document using agent (handles all document operations internally)
        redlined_result = agent.create_redlined_document(
            analysis_data=review_result.get('analysis', ''),
            document_s3_key=document_s3_key,
            bucket_type=bucket_type
        )
        
        return create_success_response({
            "message": "Document review completed successfully",
            "document_s3_key": document_s3_key,
            "analysis": review_result.get('analysis', ''),
            "thinking": review_result.get('thinking', ''),
            "citations": review_result.get('citations', []),
            "usage": review_result.get('usage', {}),
            "redlined_document": redlined_result
        })
        
    except Exception as e:
        logger.error(f"Error in document review function: {str(e)}")
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


def extract_document_content(bucket_type: str, document_s3_key: str) -> str:
    """Extract text content from a DOCX document stored in S3."""
    
    try:
        bucket_name = get_bucket_name(bucket_type)
        logger.info(f"Extracting content from {bucket_name}/{document_s3_key}")
        
        # Download the document from S3
        response = s3_client.get_object(
            Bucket=bucket_name,
            Key=document_s3_key
        )
        
        document_content = response['Body'].read()
        
        # Load the document using bayoo-docx
        doc = Document(io.BytesIO(document_content))
        
        # Extract text content from all paragraphs
        text_content = []
        
        for paragraph in doc.paragraphs:
            if paragraph.text.strip():
                text_content.append(paragraph.text.strip())
        
        # Join all paragraphs with double newlines
        full_text = '\n\n'.join(text_content)
        
        logger.info(f"Extracted {len(text_content)} paragraphs, {len(full_text)} characters")
        
        return full_text
        
    except Exception as e:
        logger.error(f"Error extracting document content: {str(e)}")
        raise Exception(f"Failed to extract document content: {str(e)}")





def create_success_response(data: Dict[str, Any]) -> Dict[str, Any]:
    """Create successful response with comprehensive CORS headers."""
    return {
        'statusCode': 200,
        'headers': {
            'Content-Type': 'application/json',
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Methods': 'GET, POST, PUT, DELETE, OPTIONS',
            'Access-Control-Allow-Headers': 'Content-Type, X-Amz-Date, Authorization, X-Api-Key, X-Amz-Security-Token',
            'Access-Control-Allow-Credentials': 'false',
            'Access-Control-Max-Age': '86400'
        },
        'body': json.dumps(data)
    }


def create_error_response(status_code: int, message: str, data: Dict[str, Any] = None) -> Dict[str, Any]:
    """Create error response with comprehensive CORS headers."""
    error_body = {'error': message, 'status_code': status_code}
    if data:
        error_body.update(data)
    
    return {
        'statusCode': status_code,
        'headers': {
            'Content-Type': 'application/json',
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Methods': 'GET, POST, PUT, DELETE, OPTIONS',
            'Access-Control-Allow-Headers': 'Content-Type, X-Amz-Date, Authorization, X-Api-Key, X-Amz-Security-Token',
            'Access-Control-Allow-Credentials': 'false',
            'Access-Control-Max-Age': '86400'
        },
        'body': json.dumps(error_body)
    } 