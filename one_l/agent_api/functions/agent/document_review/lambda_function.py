"""
Lambda function for AI-powered document review.
Handles vendor document analysis for conflicts with reference documents.
"""

import json
import boto3
import os
import logging
import sys
from typing import Dict, Any, List, Optional, Tuple
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
JOB_STATUS_TABLE = os.environ.get('ANALYSIS_RESULTS_TABLE', 'OneL-DV2-analysis-results')

DEFAULT_TERMS_PROFILE_RAW = os.environ.get('DEFAULT_TERMS_PROFILE', 'it')

def normalize_terms_profile(value: Optional[str]) -> str:
    """Normalize requested terms profile to supported values."""
    if not value:
        value = DEFAULT_TERMS_PROFILE_RAW
    normalized = (value or 'it').strip().lower()
    if normalized.startswith('gen'):
        return 'general'
    if normalized in ('it', 'its', 'technical', 'technology', 'tech'):
        return 'it'
    if normalized == 'general':
        return 'general'
    return 'it'


def resolve_terms_profile(requested_profile: Optional[str]) -> Tuple[Optional[str], str, Optional[str], List[str]]:
    """
    Resolve the requested terms profile to a concrete knowledge base ID.
    
    Returns:
        Tuple of (knowledge_base_id, resolved_profile, error_message, available_profiles)
    """
    normalized = normalize_terms_profile(requested_profile)
    general_kb = os.environ.get('KNOWLEDGE_BASE_ID_GENERAL') or os.environ.get('GENERAL_TERMS_KNOWLEDGE_BASE_ID')
    it_kb = os.environ.get('KNOWLEDGE_BASE_ID_IT') or os.environ.get('IT_TERMS_KNOWLEDGE_BASE_ID') or os.environ.get('KNOWLEDGE_BASE_ID')
    
    available_profiles = []
    if general_kb:
        available_profiles.append('general')
    if it_kb:
        available_profiles.append('it')
    
    if not available_profiles:
        return None, normalized, 'Knowledge base IDs are not configured', available_profiles
    
    if normalized == 'general':
        if general_kb:
            return general_kb, 'general', None, available_profiles
        return None, 'general', 'General terms knowledge base not configured', available_profiles
    
    # Default to IT terms profile
    if it_kb:
        return it_kb, 'it', None, available_profiles
    
    return None, 'it', 'IT terms knowledge base not configured', available_profiles

def save_job_status(job_id: str, document_s3_key: str, user_id: str, session_id: str, 
                   status: str, error: str = None, result: dict = None,
                   terms_profile: Optional[str] = None, knowledge_base_id: Optional[str] = None):
    """Save job status to DynamoDB for frontend polling"""
    try:
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
        
        if terms_profile:
            item['terms_profile'] = terms_profile
        if knowledge_base_id:
            item['knowledge_base_id'] = knowledge_base_id
        
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
        requested_terms_profile = body_data.get('terms_profile')
        
        # Validate required parameters
        if not document_s3_key:
            return create_error_response(400, "document_s3_key is required")
        
        # Get agent configuration
        knowledge_base_id, resolved_terms_profile, profile_error, available_profiles = resolve_terms_profile(requested_terms_profile)
        region = os.environ.get('REGION')
        
        if profile_error:
            status_code = 400 if requested_terms_profile else 500
            error_payload = {
                "requested_terms_profile": requested_terms_profile,
                "resolved_terms_profile": resolved_terms_profile,
                "available_terms_profiles": available_profiles
            }
            logger.error(f"Terms profile resolution error: {profile_error} | requested={requested_terms_profile} | available={available_profiles}")
            return create_error_response(status_code, profile_error, error_payload)
        
        if not knowledge_base_id:
            return create_error_response(500, "Knowledge base ID not configured")
        
        logger.info(f"Using terms profile '{resolved_terms_profile}' with knowledge base '{knowledge_base_id}' for document '{document_s3_key}'")
        
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
                "estimated_completion_time": "2-5 minutes",
                "terms_profile": resolved_terms_profile
            }
            
            # For API Gateway, return immediately and process in background
            # Generate unique job ID for tracking
            job_id = str(uuid.uuid4())
            
            # Save initial job status to DynamoDB
            save_job_status(
                job_id,
                document_s3_key,
                user_id,
                session_id,
                "processing",
                terms_profile=resolved_terms_profile,
                knowledge_base_id=knowledge_base_id
            )
            
            # Return immediate response to frontend
            immediate_response = {
                "success": True,
                "processing": True,
                "job_id": job_id,
                "message": "Document review started successfully",
                "document_s3_key": document_s3_key,
                "estimated_completion_time": "2-5 minutes",
                "status_check_interval": 10,  # seconds
                "terms_profile": resolved_terms_profile
            }
            
            # Start background processing after responding
            try:
                # Initialize Lambda client for notifications
                lambda_client = boto3.client('lambda')
                
                # Set up timeout handler to trigger cleanup before Lambda times out
                # Lambda timeout is 15 minutes, trigger cleanup at 14 minutes
                import threading
                cleanup_triggered = threading.Event()
                timeout_timer = None
                
                def timeout_cleanup_handler():
                    """Trigger cleanup and send timeout notification when approaching Lambda timeout."""
                    # Use double-check pattern to prevent race condition with successful completion
                    if not cleanup_triggered.is_set():
                        cleanup_triggered.set()
                        
                        # Check if job is already completed before marking as failed (prevents race condition)
                        try:
                            table = dynamodb.Table(JOB_STATUS_TABLE)
                            response = table.get_item(
                                Key={'analysis_id': f"job_{job_id}"}
                            )
                            
                            if 'Item' in response:
                                current_status = response['Item'].get('status')
                                # If job is already completed or failed, don't overwrite it
                                if current_status in ('completed', 'failed'):
                                    logger.info(f"Job {job_id} already has status '{current_status}', skipping timeout handler")
                                    return
                        except Exception as check_error:
                            logger.warning(f"Could not check job status before timeout handler: {check_error}")
                            # Continue with timeout handling if we can't check status
                        
                        logger.warning("Approaching Lambda timeout, triggering cleanup and sending timeout notification")
                        
                        # Save job status as failed with timeout error
                        timeout_error_message = "Task timed out after 15 minutes"
                        try:
                            save_job_status(
                                job_id,
                                document_s3_key,
                                user_id,
                                session_id,
                                "failed",
                                error=timeout_error_message,
                                terms_profile=resolved_terms_profile,
                                knowledge_base_id=knowledge_base_id
                            )
                            logger.info(f"Saved timeout failure status for job {job_id}")
                        except Exception as save_error:
                            logger.error(f"Failed to save timeout status: {save_error}")
                        
                        # Send WebSocket notification for timeout failure
                        try:
                            notification_function_name = f"{os.environ.get('AWS_LAMBDA_FUNCTION_NAME', '').replace('-document-review', '-websocket-notification')}"
                            timeout_notification_payload = {
                                'notification_type': 'job_completed',
                                'job_id': job_id,
                                'user_id': user_id,
                                'session_id': session_id,
                                'data': {
                                    'status': 'failed',
                                    'error': timeout_error_message,
                                    'redlined_document': {
                                        'success': False,
                                        'error': timeout_error_message
                                    },
                                    'message': 'Document processing timed out'
                                }
                            }
                            
                            lambda_client.invoke(
                                FunctionName=notification_function_name,
                                InvocationType='Event',  # Async call
                                Payload=json.dumps(timeout_notification_payload)
                            )
                            logger.info(f"Sent timeout notification for job {job_id}")
                        except Exception as notify_error:
                            logger.error(f"Failed to send timeout notification: {notify_error}")
                        
                        # Cleanup session documents (critical - must complete before Lambda times out)
                        if session_id and user_id:
                            try:
                                from agent_api.agent.tools import _cleanup_session_documents
                                # Cleanup is synchronous and should complete within the 2-minute buffer
                                # If it fails, log error but don't block timeout notification
                                cleanup_result = _cleanup_session_documents(session_id, user_id)
                                if cleanup_result.get('success'):
                                    logger.info(f"Timeout cleanup completed successfully: {cleanup_result.get('message', '')}")
                                else:
                                    logger.error(f"Timeout cleanup failed: {cleanup_result.get('error', 'Unknown error')}")
                                    # Note: Documents may not be deleted, but timeout notification was sent
                            except Exception as cleanup_error:
                                logger.error(f"Timeout cleanup error (documents may not be deleted): {cleanup_error}")
                                # Continue - timeout notification already sent, cleanup failure is logged
                    else:
                        # Cleanup already triggered (likely by successful completion)
                        logger.info(f"Timeout handler skipped - cleanup already triggered for job {job_id}")
                
                # Schedule cleanup 2 minutes before timeout (13 minutes) to ensure cleanup completes
                # Only set up timer if context is available
                if context and hasattr(context, 'get_remaining_time_in_millis'):
                    # Calculate remaining time and schedule cleanup 120 seconds before timeout
                    # This gives us 2 minutes buffer to ensure cleanup completes before Lambda is killed
                    remaining_ms = context.get_remaining_time_in_millis()
                    remaining_seconds = remaining_ms / 1000.0
                    cleanup_delay = max(120.0, remaining_seconds - 120.0)  # At least 120 seconds before timeout
                    timeout_timer = threading.Timer(cleanup_delay, timeout_cleanup_handler)
                    timeout_timer.daemon = True
                    timeout_timer.start()
                    logger.info(f"Scheduled timeout cleanup in {cleanup_delay} seconds (2 minutes before Lambda timeout)")
                
                # Clear knowledge base cache for fresh document review session
                from agent_api.agent.tools import clear_knowledge_base_cache
                clear_knowledge_base_cache()
                
                # Initialize the document review agent (composition pattern)
                agent = Agent(knowledge_base_id, region)
                
                # Update job status
                save_job_status(
                    job_id,
                    document_s3_key,
                    user_id,
                    session_id,
                    "analyzing",
                    terms_profile=resolved_terms_profile,
                    knowledge_base_id=knowledge_base_id
                )
                
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
                    # Cancel timeout cleanup timer if it exists
                    if timeout_timer is not None:
                        timeout_timer.cancel()
                    
                    # Cleanup reference documents on review failure
                    if session_id and user_id:
                        try:
                            from agent_api.agent.tools import _cleanup_session_documents
                            cleanup_result = _cleanup_session_documents(session_id, user_id)
                            logger.info(f"Cleanup after review failure: {cleanup_result}")
                        except Exception as cleanup_error:
                            logger.error(f"Session cleanup error after review failure: {cleanup_error}")
                    
                    save_job_status(
                        job_id,
                        document_s3_key,
                        user_id,
                        session_id,
                        "failed",
                        error=review_result.get('error', 'Unknown error'),
                        terms_profile=resolved_terms_profile,
                        knowledge_base_id=knowledge_base_id
                    )
                    return create_success_response(immediate_response)
                
                # Update job status
                save_job_status(
                    job_id,
                    document_s3_key,
                    user_id,
                    session_id,
                    "generating_redline",
                    terms_profile=resolved_terms_profile,
                    knowledge_base_id=knowledge_base_id
                )
                
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
                try:
                    redlined_result = agent.create_redlined_document(
                        analysis_data=review_result.get('analysis', ''),
                        document_s3_key=document_s3_key,
                        bucket_type=bucket_type,
                        session_id=session_id,
                        user_id=user_id
                    )
                except Exception as redline_error:
                    logger.error(f"Error creating redlined document: {str(redline_error)}", exc_info=True)
                    
                    # Cancel timeout cleanup timer if it exists
                    if timeout_timer is not None:
                        timeout_timer.cancel()
                    
                    # Cleanup reference documents on redline failure
                    if session_id and user_id:
                        try:
                            from agent_api.agent.tools import _cleanup_session_documents
                            cleanup_result = _cleanup_session_documents(session_id, user_id)
                            logger.info(f"Cleanup after redline failure: {cleanup_result}")
                        except Exception as cleanup_error:
                            logger.error(f"Session cleanup error after redline failure: {cleanup_error}")
                    
                    save_job_status(
                        job_id,
                        document_s3_key,
                        user_id,
                        session_id,
                        "failed",
                        error=f"Failed to create redlined document: {str(redline_error)}",
                        terms_profile=resolved_terms_profile,
                        knowledge_base_id=knowledge_base_id
                    )
                    return create_success_response(immediate_response)
                
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
                    "saved_to_database": save_result.get('success', False),
                    "terms_profile": resolved_terms_profile
                }
                save_job_status(
                    job_id,
                    document_s3_key,
                    user_id,
                    session_id,
                    "completed",
                    result=completion_data,
                    terms_profile=resolved_terms_profile,
                    knowledge_base_id=knowledge_base_id
                )
                
                # Cancel timeout cleanup timer since we completed successfully
                if timeout_timer is not None:
                    timeout_timer.cancel()
                    cleanup_triggered.set()
                    logger.info("Cancelled timeout cleanup timer - processing completed successfully")
                
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
                                'message': 'Document analysis completed successfully',
                                'terms_profile': resolved_terms_profile
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
                
                # Cancel timeout cleanup timer if it exists
                if 'timeout_timer' in locals() and timeout_timer is not None:
                    timeout_timer.cancel()
                
                # Cleanup reference documents on any processing error
                if session_id and user_id:
                    try:
                        from agent_api.agent.tools import _cleanup_session_documents
                        cleanup_result = _cleanup_session_documents(session_id, user_id)
                        logger.info(f"Cleanup after background processing error: {cleanup_result}")
                    except Exception as cleanup_error:
                        logger.error(f"Session cleanup error after background processing error: {cleanup_error}")
                
                save_job_status(
                    job_id,
                    document_s3_key,
                    user_id,
                    session_id,
                    "failed",
                    error=str(e),
                    terms_profile=resolved_terms_profile,
                    knowledge_base_id=knowledge_base_id
                )
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
                "saved_to_database": save_result.get('success', False),
                "terms_profile": resolved_terms_profile
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