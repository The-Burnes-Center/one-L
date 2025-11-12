"""
Lambda function for AI-powered document review.
Handles vendor document analysis for conflicts with reference documents.
"""

import json
import boto3
import os
import logging
import sys
from typing import Dict, Any, List
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

# DynamoDB operations are now handled in tools.py

# Initialize DynamoDB for job status tracking
dynamodb = boto3.resource('dynamodb')
# ANALYSIS_RESULTS_TABLE should always be set by CDK - no fallback to avoid hardcoded stack names
JOB_STATUS_TABLE = os.environ.get('ANALYSIS_RESULTS_TABLE')
if not JOB_STATUS_TABLE:
    logger.warning("ANALYSIS_RESULTS_TABLE environment variable not set - job status tracking may fail")

def save_job_status(job_id: str, document_s3_key: str, user_id: str, session_id: str, 
                   status: str, error: str = None, result: dict = None):
    """Save job status to DynamoDB for frontend polling"""
    try:
        if not JOB_STATUS_TABLE:
            logger.error("JOB_STATUS_TABLE (ANALYSIS_RESULTS_TABLE) not set - cannot save job status")
            return
        
        table = dynamodb.Table(JOB_STATUS_TABLE)
        
        item = {
            'analysis_id': f"job_{job_id}",
            'timestamp': datetime.now().isoformat(),
            'job_id': job_id,
            'document_s3_key': document_s3_key,
            'user_id': user_id,
            'session_id': session_id,
            'status': status,
            'updated_at': datetime.now().isoformat()
        }
        
        if error:
            item['error'] = error
        if result:
            item['result'] = result
            
        table.put_item(Item=item)

        
    except Exception as e:
        logger.error(f"Failed to save job status: {e}")


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Lambda function to review vendor documents using AI.
    
    Expected event format from API Gateway:
    {
        "body": "{\"document_s3_key\": \"path/to/document.docx\", \"bucket_type\": \"user_documents\"}"
    }
    """
    
    try:
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
        session_id = body_data.get('session_id')
        user_id = body_data.get('user_id')
        

        
        # Validate required parameters
        if not document_s3_key:
            return create_error_response(400, "document_s3_key is required")
        
        # Get agent configuration
        knowledge_base_id = os.environ.get('KNOWLEDGE_BASE_ID')
        region = os.environ.get('REGION')
        
        if not knowledge_base_id:
            return create_error_response(500, "Knowledge base ID not configured")
        
        # Check if this is invoked from API Gateway (has API Gateway timeout constraint)
        is_api_gateway_request = 'httpMethod' in event and 'headers' in event
        
        if is_api_gateway_request:
            # For API Gateway requests, start async processing and return immediately

            
            # Start background processing using the remaining Lambda execution time
            import threading
            
            # Create response immediately 
            response_data = {
                "message": "Document review started - processing in background",
                "document_s3_key": document_s3_key,
                "bucket_type": bucket_type,
                "processing": True,
                "estimated_completion_time": "2-5 minutes"
            }
            
            # For API Gateway, return immediately and process in background
            # Generate unique job ID for tracking
            job_id = str(uuid.uuid4())
            
            # Save initial job status to DynamoDB
            save_job_status(job_id, document_s3_key, user_id, session_id, "processing")
            
            # Return immediate response to frontend
            immediate_response = {
                "success": True,
                "processing": True,
                "job_id": job_id,
                "message": "Document review started successfully",
                "document_s3_key": document_s3_key,
                "estimated_completion_time": "2-5 minutes",
                "status_check_interval": 10  # seconds
            }
            
            # Start background processing after responding
            try:
                # Initialize Lambda client for notifications
                lambda_client = boto3.client('lambda')
                
                # Clear knowledge base cache for fresh document review session
                from agent_api.agent.tools import clear_knowledge_base_cache
                clear_knowledge_base_cache()
                
                # Initialize the document review agent (composition pattern)
                agent = Agent(knowledge_base_id, region)
                
                # Update job status
                save_job_status(job_id, document_s3_key, user_id, session_id, "analyzing")
                
                # Send WebSocket progress notification
                try:
                    notification_function_name = f"{os.environ.get('AWS_LAMBDA_FUNCTION_NAME', '').replace('-document-review', '-websocket-notification')}"
                    progress_payload = {
                        'notification_type': 'job_progress',
                        'job_id': job_id,
                        'user_id': user_id,
                        'session_id': session_id,
                        'data': {
                            'status': 'analyzing',
                            'progress': 25,
                            'message': 'Analyzing document with AI...'
                        }
                    }
                    
                    lambda_client.invoke(
                        FunctionName=notification_function_name,
                        InvocationType='Event',
                        Payload=json.dumps(progress_payload)
                    )
                except Exception as e:
                    pass
                
                # Run the document review with direct document attachment
                review_result = agent.review_document(bucket_type, document_s3_key)
                
                if not review_result.get('success', False):
                    save_job_status(job_id, document_s3_key, user_id, session_id, "failed", 
                                  error=review_result.get('error', 'Unknown error'))
                    return create_success_response(immediate_response)
                
                # Update job status
                save_job_status(job_id, document_s3_key, user_id, session_id, "generating_redline")
                
                # Send WebSocket progress notification
                try:
                    progress_payload = {
                        'notification_type': 'job_progress',
                        'job_id': job_id,
                        'user_id': user_id,
                        'session_id': session_id,
                        'data': {
                            'status': 'generating_redline',
                            'progress': 75,
                            'message': 'Generating redlined document...'
                        }
                    }
                    
                    lambda_client.invoke(
                        FunctionName=notification_function_name,
                        InvocationType='Event',
                        Payload=json.dumps(progress_payload)
                    )
                except Exception as e:
                    pass
                
                # Create redlined document using agent (handles all document operations internally)
                redlined_result = agent.create_redlined_document(
                    analysis_data=review_result.get('analysis', ''),
                    document_s3_key=document_s3_key,
                    bucket_type=bucket_type,
                    session_id=session_id,
                    user_id=user_id
                )
                
                # Generate unique analysis ID and save results to DynamoDB using tools
                from agent_api.agent.tools import save_analysis_to_dynamodb
                
                analysis_id = str(uuid.uuid4())
                save_result = save_analysis_to_dynamodb(
                    analysis_id=analysis_id,
                    document_s3_key=document_s3_key,
                    analysis_data=review_result.get('analysis', ''),
                    bucket_type=bucket_type,
                    usage_data=review_result.get('usage', {}),
                    thinking=review_result.get('thinking', ''),
                    citations=review_result.get('citations', []),
                    session_id=session_id,
                    user_id=user_id,
                    redlined_result=redlined_result
                )
                


                
                # Update job status to completed
                completion_data = {
                    "analysis_id": analysis_id,
                    "analysis": review_result.get('analysis', ''),
                    "redlined_document": redlined_result,
                    "saved_to_database": save_result.get('success', False)
                }
                save_job_status(job_id, document_s3_key, user_id, session_id, "completed", 
                              result=completion_data)
                
                # Mark session as having results (processed documents)
                if session_id and user_id:
                    try:
                        
                        # Call session management to mark session as having results
                        session_payload = {
                            'httpMethod': 'PUT',
                            'body': json.dumps({
                                'action': 'mark_results',
                                'session_id': session_id,
                                'user_id': user_id
                            })
                        }
                        
                        # Invoke session management function asynchronously
                        lambda_client.invoke(
                            FunctionName=f"{os.environ.get('AWS_LAMBDA_FUNCTION_NAME', '').replace('-document-review', '-session-management')}",
                            InvocationType='Event',  # Async call
                            Payload=json.dumps(session_payload)
                        )

                        
                        # Send WebSocket notification for job completion
                        notification_function_name = f"{os.environ.get('AWS_LAMBDA_FUNCTION_NAME', '').replace('-document-review', '-websocket-notification')}"
                        notification_payload = {
                            'notification_type': 'job_completed',
                            'job_id': job_id,
                            'user_id': user_id,
                            'session_id': session_id,
                            'data': {
                                'status': 'completed',
                                'analysis_id': analysis_id,
                                'redlined_document': redlined_result,
                                'message': 'Document analysis completed successfully'
                            }
                        }
                        
                        # Invoke WebSocket notification function asynchronously
                        lambda_client.invoke(
                            FunctionName=notification_function_name,
                            InvocationType='Event',  # Async call
                            Payload=json.dumps(notification_payload)
                        )

                        
                    except Exception as e:
                        pass
                
                # Return immediate response (API Gateway already closed connection)
                return create_success_response(immediate_response)
                
            except Exception as e:
                logger.error(f"Error during background processing: {str(e)}", exc_info=True)
                save_job_status(job_id, document_s3_key, user_id, session_id, "failed", 
                              error=str(e))
                return create_success_response(immediate_response)
            
        else:
            # For direct Lambda invocation, process synchronously
            
            # Clear knowledge base cache for fresh document review session
            from agent_api.agent.tools import clear_knowledge_base_cache
            clear_knowledge_base_cache()
            
            # Initialize the document review agent (composition pattern)
            agent = Agent(knowledge_base_id, region)
            
            # Run the document review with direct document attachment
            review_result = agent.review_document(bucket_type, document_s3_key)
            
            if not review_result.get('success', False):
                return create_error_response(500, f"Document review failed: {review_result.get('error', 'Unknown error')}")
            
            # Create redlined document using agent (handles all document operations internally)
            redlined_result = agent.create_redlined_document(
                analysis_data=review_result.get('analysis', ''),
                document_s3_key=document_s3_key,
                bucket_type=bucket_type,
                session_id=session_id,
                user_id=user_id
            )
            
            # Generate unique analysis ID and save results to DynamoDB using tools
            from agent_api.agent.tools import save_analysis_to_dynamodb
            
            analysis_id = str(uuid.uuid4())
            save_result = save_analysis_to_dynamodb(
                analysis_id=analysis_id,
                document_s3_key=document_s3_key,
                analysis_data=review_result.get('analysis', ''),
                bucket_type=bucket_type,
                usage_data=review_result.get('usage', {}),
                thinking=review_result.get('thinking', ''),
                citations=review_result.get('citations', []),
                session_id=session_id,
                user_id=user_id,
                redlined_result=redlined_result
            )
            
            # Include save status in response
            response_data = {
                "message": "Document review completed successfully",
                "analysis_id": analysis_id,
                "document_s3_key": document_s3_key,
                "analysis": review_result.get('analysis', ''),
                "thinking": review_result.get('thinking', ''),
                "citations": review_result.get('citations', []),
                "usage": review_result.get('usage', {}),
                "redlined_document": redlined_result,
                "saved_to_database": save_result.get('success', False)
            }
            
            if not save_result.get('success', False):

                response_data["database_warning"] = f"Analysis completed but failed to save: {save_result.get('error', 'Unknown error')}"
            
            return create_success_response(response_data)
        
    except Exception as e:
        logger.error(f"Error in document review function: {str(e)}", exc_info=True)
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

        
        # Download the document from S3
        response = s3_client.get_object(
            Bucket=bucket_name,
            Key=document_s3_key
        )
        
        document_content = response['Body'].read()
        
        # Load the document using docx library
        doc = Document(io.BytesIO(document_content))
        
        # Extract text content from all paragraphs
        text_content = []
        
        for paragraph in doc.paragraphs:
            if paragraph.text.strip():
                text_content.append(paragraph.text.strip())
        
        # Join all paragraphs with double newlines
        full_text = '\n\n'.join(text_content)
        

        
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