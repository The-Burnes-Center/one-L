import json
import boto3
import logging
import os
from datetime import datetime, timezone
import uuid
from typing import Dict, Any
from decimal import Decimal

# Set up logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Initialize AWS clients
s3_client = boto3.client('s3')
dynamodb = boto3.resource('dynamodb')

# Environment variables
KNOWLEDGE_BUCKET = os.environ.get('KNOWLEDGE_BUCKET')
USER_DOCUMENTS_BUCKET = os.environ.get('USER_DOCUMENTS_BUCKET')
AGENT_PROCESSING_BUCKET = os.environ.get('AGENT_PROCESSING_BUCKET')
SESSIONS_TABLE = os.environ.get('SESSIONS_TABLE', 'one-l-sessions')
ANALYSIS_RESULTS_TABLE = os.environ.get('ANALYSIS_RESULTS_TABLE')

def convert_decimals(obj):
    """Recursively convert Decimal types to int or float for JSON serialization"""
    if isinstance(obj, Decimal):
        # Convert Decimal to int if it's a whole number, otherwise float
        if obj % 1 == 0:
            return int(obj)
        return float(obj)
    elif isinstance(obj, dict):
        return {key: convert_decimals(value) for key, value in obj.items()}
    elif isinstance(obj, list):
        return [convert_decimals(item) for item in obj]
    return obj

def create_cors_response(status_code: int, body: dict) -> dict:
    """Create a response with CORS headers"""
    # Convert any Decimal types to native Python types for JSON serialization
    body = convert_decimals(body)
    return {
        'statusCode': status_code,
        'headers': {
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Methods': 'GET, POST, PUT, DELETE, OPTIONS',
            'Access-Control-Allow-Headers': 'Content-Type, Authorization, X-Requested-With, X-Amz-Date, X-Api-Key, X-Amz-Security-Token',
            'Access-Control-Allow-Credentials': 'false',
            'Content-Type': 'application/json'
        },
        'body': json.dumps(body)
    }

def create_session(user_id: str, cognito_session_id: str = None) -> Dict[str, Any]:
    """Create a new session for a user"""
    try:
        session_id = str(uuid.uuid4())
        timestamp = datetime.now(timezone.utc).isoformat()
        
        # Create session directory structure in S3
        session_prefix = f"sessions/{user_id}/{session_id}/"
        
        # Create directories for different file types
        directories = [
            f"{session_prefix}vendor-submissions/",
            f"{session_prefix}output/",
            f"{session_prefix}analysis/"
        ]
        
        # Create empty objects to establish directory structure
        for directory in directories:
            s3_client.put_object(
                Bucket=AGENT_PROCESSING_BUCKET,
                Key=f"{directory}.keep",
                Body=b'',
                ContentType='text/plain'
            )
        
        # Store session metadata
        session_data = {
            'session_id': session_id,
            'user_id': user_id,
            'cognito_session_id': cognito_session_id,
            'created_at': timestamp,
            'updated_at': timestamp,
            'title': f"New Session - {datetime.now().strftime('%b %d, %Y %I:%M %p')}",
            'status': 'active',
            'document_count': 0,
            'has_results': False,  # Track if session has processed documents
            'last_activity': timestamp,
            's3_prefix': session_prefix
        }
        
        # Try to store in DynamoDB
        try:
            table = dynamodb.Table(SESSIONS_TABLE)
            table.put_item(Item=session_data)
            logger.info(f"Session metadata stored in DynamoDB: {session_id}")
        except Exception as e:
            logger.warning(f"Could not store session in DynamoDB: {e}")
        
        logger.info(f"Created session {session_id} for user {user_id}")
        
        return {
            'success': True,
            'session': session_data
        }
        
    except Exception as e:
        logger.error(f"Error creating session: {e}")
        return {
            'success': False,
            'error': str(e)
        }

def get_user_sessions(user_id: str, filter_by_results: bool = False) -> Dict[str, Any]:
    """Get all sessions for a user, optionally filtered by whether they have results"""
    try:
        # Validate environment variables
        if not SESSIONS_TABLE:
            logger.error("SESSIONS_TABLE environment variable not set")
            return {
                'success': False,
                'error': 'Configuration error: Sessions table not configured',
                'sessions': []
            }
        
        # Try to get from DynamoDB
        try:
            table = dynamodb.Table(SESSIONS_TABLE)
            sessions = []
            last_evaluated_key = None
            
            # Handle pagination for scan operation (DynamoDB has 1MB limit per page)
            while True:
                scan_kwargs = {
                    'FilterExpression': 'user_id = :user_id',
                    'ExpressionAttributeValues': {':user_id': user_id}
                }
                
                if last_evaluated_key:
                    scan_kwargs['ExclusiveStartKey'] = last_evaluated_key
                
                response = table.scan(**scan_kwargs)
                sessions.extend(response.get('Items', []))
                
                # Check if there are more pages
                last_evaluated_key = response.get('LastEvaluatedKey')
                if not last_evaluated_key:
                    break
            
            # Filter sessions with results if requested
            if filter_by_results:
                sessions = [s for s in sessions if s.get('has_results', False)]
            
            # Sort by created_at descending (handle missing or invalid dates)
            try:
                sessions.sort(key=lambda x: x.get('created_at', ''), reverse=True)
            except (TypeError, AttributeError) as sort_error:
                logger.warning(f"Error sorting sessions by created_at: {sort_error}")
                # Fallback: sort by updated_at if available, otherwise keep original order
                try:
                    sessions.sort(key=lambda x: x.get('updated_at', ''), reverse=True)
                except (TypeError, AttributeError):
                    pass  # Keep original order if sorting fails
            
            logger.info(f"Retrieved {len(sessions)} sessions for user {user_id} (filter_by_results: {filter_by_results})")
            
            return {
                'success': True,
                'sessions': sessions
            }
            
        except Exception as e:
            error_type = type(e).__name__
            error_msg = str(e)
            
            # Log the full error for debugging
            logger.error(f"Could not retrieve sessions from DynamoDB: {error_type}: {error_msg}")
            logger.error(f"Full error details: {repr(e)}")
            
            # Check if it's a specific DynamoDB error
            if 'ResourceNotFoundException' in error_type or 'does not exist' in error_msg:
                logger.error(f"SESSIONS_TABLE '{SESSIONS_TABLE}' does not exist")
                return {
                    'success': False,
                    'error': f'Sessions table not found: {SESSIONS_TABLE}',
                    'sessions': []
                }
            elif 'AccessDeniedException' in error_type or 'Access denied' in error_msg:
                logger.error(f"Access denied to SESSIONS_TABLE '{SESSIONS_TABLE}'")
                return {
                    'success': False,
                    'error': 'Access denied to sessions table',
                    'sessions': []
                }
            
            # For other errors, return empty list gracefully (don't fail completely)
            logger.warning(f"Returning empty sessions list due to error: {error_msg}")
            return {
                'success': True,
                'sessions': []
            }
            
    except Exception as e:
        logger.error(f"Unexpected error getting user sessions: {e}", exc_info=True)
        return {
            'success': False,
            'error': f'Unexpected error: {str(e)}',
            'sessions': []
        }

def update_session_title(session_id: str, user_id: str, title: str) -> Dict[str, Any]:
    """Update session title"""
    try:
        # Validate environment variables
        if not SESSIONS_TABLE:
            logger.error("SESSIONS_TABLE environment variable not set")
            return {
                'success': False,
                'error': 'Configuration error: Sessions table not configured'
            }
        
        table = dynamodb.Table(SESSIONS_TABLE)
        
        # Update the session
        response = table.update_item(
            Key={'session_id': session_id},
            UpdateExpression='SET title = :title, updated_at = :updated_at, last_activity = :last_activity',
            ExpressionAttributeValues={
                ':title': title,
                ':updated_at': datetime.now(timezone.utc).isoformat(),
                ':last_activity': datetime.now(timezone.utc).isoformat(),
                ':user_id': user_id
            },
            ConditionExpression='user_id = :user_id',
            ReturnValues='ALL_NEW'
        )
        
        logger.info(f"Updated session title: {session_id}")
        
        return {
            'success': True,
            'session': response.get('Attributes', {})
        }
        
    except Exception as e:
        error_type = type(e).__name__
        error_msg = str(e)
        logger.error(f"Error updating session title ({error_type}): {error_msg}")
        logger.error(f"Full error details: {repr(e)}")
        
        # Provide more specific error messages
        if 'ConditionalCheckFailedException' in error_type:
            return {
                'success': False,
                'error': 'Session not found or access denied'
            }
        elif 'ResourceNotFoundException' in error_type:
            return {
                'success': False,
                'error': f'Sessions table not found: {SESSIONS_TABLE}'
            }
        
        return {
            'success': False,
            'error': f'Failed to update session title: {error_msg}'
        }

def mark_session_with_results(session_id: str, user_id: str) -> Dict[str, Any]:
    """Mark a session as having processed results (documents)"""
    try:
        table = dynamodb.Table(SESSIONS_TABLE)
        
        # First, check if session exists and belongs to user
        try:
            existing_session = table.get_item(Key={'session_id': session_id})
            if 'Item' not in existing_session:
                logger.warning(f"Session {session_id} does not exist, cannot mark as having results")
                return {
                    'success': False,
                    'error': 'Session not found'
                }
            
            if existing_session['Item'].get('user_id') != user_id:
                logger.warning(f"Session {session_id} does not belong to user {user_id}")
                return {
                    'success': False,
                    'error': 'Session access denied'
                }
        except Exception as check_error:
            logger.error(f"Error checking session existence: {check_error}")
            # Continue anyway - let the update_item handle it
        
        # Update session to mark it has results
        # Use ADD for document_count (works even if attribute doesn't exist) and SET for other fields
        try:
            response = table.update_item(
                Key={'session_id': session_id},
                UpdateExpression='SET has_results = :has_results, updated_at = :updated_at, last_activity = :last_activity ADD document_count :inc',
                ExpressionAttributeValues={
                    ':has_results': True,
                    ':updated_at': datetime.now(timezone.utc).isoformat(),
                    ':last_activity': datetime.now(timezone.utc).isoformat(),
                    ':inc': 1,
                    ':user_id': user_id
                },
                ConditionExpression='user_id = :user_id',
                ReturnValues='ALL_NEW'
            )
        except Exception as update_error:
            error_type = type(update_error).__name__
            # If the conditional check fails or document_count causes issues, try without the increment
            if 'ConditionalCheckFailedException' in error_type or 'document_count' in str(update_error):
                logger.warning(f"Update failed for session {session_id}, trying without document_count increment: {update_error}")
                try:
                    response = table.update_item(
                        Key={'session_id': session_id},
                        UpdateExpression='SET has_results = :has_results, updated_at = :updated_at, last_activity = :last_activity',
                        ExpressionAttributeValues={
                            ':has_results': True,
                            ':updated_at': datetime.now(timezone.utc).isoformat(),
                            ':last_activity': datetime.now(timezone.utc).isoformat(),
                            ':user_id': user_id
                        },
                        ConditionExpression='user_id = :user_id',
                        ReturnValues='ALL_NEW'
                    )
                except Exception as retry_error:
                    logger.error(f"Error marking session with results (retry): {retry_error}")
                    return {
                        'success': False,
                        'error': str(retry_error)
                    }
            else:
                raise
        
        logger.info(f"Marked session {session_id} as having results")
        
        return {
            'success': True,
            'session': response.get('Attributes', {})
        }
        
    except Exception as e:
        error_type = type(e).__name__
        logger.error(f"Error marking session with results ({error_type}): {e}")
        
        # Handle ConditionalCheckFailedException gracefully
        if 'ConditionalCheckFailedException' in error_type:
            logger.warning(f"Session {session_id} conditional check failed - session may not exist or user_id mismatch")
            return {
                'success': False,
                'error': 'Session not found or access denied',
                'error_type': 'ConditionalCheckFailedException'
            }
        
        return {
            'success': False,
            'error': str(e)
        }



def delete_session(session_id: str, user_id: str) -> Dict[str, Any]:
    """Delete a session and its associated files"""
    try:
        # First, get session info to find S3 prefix
        table = dynamodb.Table(SESSIONS_TABLE)
        
        try:
            response = table.get_item(Key={'session_id': session_id})
            session = response.get('Item')
            
            if not session or session.get('user_id') != user_id:
                return {
                    'success': False,
                    'error': 'Session not found or access denied'
                }
                
            s3_prefix = session.get('s3_prefix', f"sessions/{user_id}/{session_id}/")
            
            # Delete S3 objects
            try:
                paginator = s3_client.get_paginator('list_objects_v2')
                for page in paginator.paginate(Bucket=AGENT_PROCESSING_BUCKET, Prefix=s3_prefix):
                    if 'Contents' in page:
                        objects = [{'Key': obj['Key']} for obj in page['Contents']]
                        if objects:
                            s3_client.delete_objects(
                                Bucket=AGENT_PROCESSING_BUCKET,
                                Delete={'Objects': objects}
                            )
                            
                logger.info(f"Deleted S3 objects for session {session_id}")
                
            except Exception as e:
                logger.warning(f"Error deleting S3 objects: {e}")
            
            # Delete from DynamoDB
            table.delete_item(
                Key={'session_id': session_id},
                ConditionExpression='user_id = :user_id',
                ExpressionAttributeValues={':user_id': user_id}
            )
            
            logger.info(f"Deleted session: {session_id}")
            
            return {
                'success': True,
                'message': 'Session deleted successfully'
            }
            
        except Exception as e:
            logger.error(f"Error deleting session: {e}")
            return {
                'success': False,
                'error': str(e)
            }
            
    except Exception as e:
        logger.error(f"Error in delete_session: {e}")
        return {
            'success': False,
            'error': str(e)
        }

def get_job_status(job_id: str, user_id: str) -> Dict[str, Any]:
    """Get job status for document processing"""
    try:
        # Validate environment variables
        if not ANALYSIS_RESULTS_TABLE:
            logger.error("ANALYSIS_RESULTS_TABLE environment variable not set")
            return {
                'success': False,
                'error': 'Configuration error: Analysis results table not configured'
            }
        
        # Use the same DynamoDB table structure as analysis results
        table = dynamodb.Table(ANALYSIS_RESULTS_TABLE)
        
        response = table.get_item(
            Key={'analysis_id': f"job_{job_id}"}
        )
        
        if 'Item' not in response:
            return {
                'success': False,
                'error': 'Job not found'
            }
        
        job_data = response['Item']
        
        # Verify user owns this job
        if job_data.get('user_id') != user_id:
            return {
                'success': False,
                'error': 'Access denied'
            }
        
        return {
            'success': True,
            'job': {
                'job_id': job_data.get('job_id'),
                'status': job_data.get('status'),
                'document_s3_key': job_data.get('document_s3_key'),
                'updated_at': job_data.get('updated_at'),
                'error': job_data.get('error'),
                'result': job_data.get('result')
            }
        }
        
    except Exception as e:
        logger.error(f"Error getting job status: {e}")
        return {
            'success': False,
            'error': str(e)
        }

def get_session_analysis_results(session_id: str, user_id: str) -> Dict[str, Any]:
    """Get all analysis results for a specific session"""
    try:
        # Validate environment variables
        if not ANALYSIS_RESULTS_TABLE:
            logger.error("ANALYSIS_RESULTS_TABLE environment variable not set")
            return {
                'success': False,
                'error': 'Configuration error: Analysis results table not configured'
            }
        
        # Get analysis results from DynamoDB
        table = dynamodb.Table(ANALYSIS_RESULTS_TABLE)
        
        # Scan for items with this session_id (since session_id is not the primary key)
        response = table.scan(
            FilterExpression='session_id = :session_id AND user_id = :user_id',
            ExpressionAttributeValues={
                ':session_id': session_id,
                ':user_id': user_id
            }
        )
        
        items = response.get('Items', [])
        
        # Filter out job status entries (those with analysis_id starting with "job_")
        analysis_results = [
            item for item in items 
            if not item.get('analysis_id', '').startswith('job_')
        ]
        
        # Sort by timestamp descending (most recent first)
        analysis_results.sort(key=lambda x: x.get('timestamp', ''), reverse=True)
        
        # Transform the data for frontend consumption
        formatted_results = []
        for result in analysis_results:
            formatted_result = {
                'analysis_id': result.get('analysis_id'),
                'document_s3_key': result.get('document_s3_key'),
                'timestamp': result.get('timestamp'),
                'conflicts_count': result.get('conflicts_count', 0),
                'conflicts': result.get('conflicts', []),
                'document_name': result.get('document_s3_key', '').split('/')[-1] if result.get('document_s3_key') else 'Unknown Document',
                'redlined_document_s3_key': result.get('redlined_document_s3_key'),  # Add missing field for download
                'analysis_data': result.get('analysis_data', '')  # Add analysis data for preview
            }
            logger.info(f"SESSION: Storing analysis result - Conflicts: {result.get('conflicts_count', 0)}")
            logger.info(f"SESSION: Redlined document: {formatted_result.get('redlined_document_s3_key')}")    
         
            formatted_results.append(formatted_result)
        
        logger.info(f"Retrieved {len(formatted_results)} analysis results for session {session_id}")
        
        return {
            'success': True,
            'session_id': session_id,
            'results': formatted_results,
            'total_results': len(formatted_results)
        }
        
    except Exception as e:
        logger.error(f"Error getting session analysis results: {e}")
        return {
            'success': False,
            'error': str(e)
        }

def lambda_handler(event, context):
    """Main Lambda handler for session management"""
    
    # Handle CORS preflight requests
    if event.get('httpMethod') == 'OPTIONS':
        return {
            'statusCode': 200,
            'headers': {
                'Access-Control-Allow-Origin': '*',
                'Access-Control-Allow-Methods': 'GET, POST, PUT, DELETE, OPTIONS',
                'Access-Control-Allow-Headers': 'Content-Type, Authorization, X-Requested-With, X-Amz-Date, X-Api-Key, X-Amz-Security-Token',
                'Access-Control-Allow-Credentials': 'false',
                'Access-Control-Max-Age': '86400'
            },
            'body': ''
        }
    
    try:
        # Parse request
        http_method = event.get('httpMethod', 'GET')
        query_parameters = event.get('queryStringParameters') or {}
        
        # Parse body for POST/PUT requests
        body = {}
        if event.get('body'):
            try:
                body = json.loads(event['body'])
            except json.JSONDecodeError:
                return create_cors_response(400, {'error': 'Invalid JSON in request body'})
        
        # Extract user information
        user_id = body.get('user_id') or query_parameters.get('user_id')
        cognito_session_id = body.get('cognito_session_id') or query_parameters.get('cognito_session_id')
        
        if not user_id:
            return create_cors_response(400, {'error': 'user_id is required'})
        
        # Get action from query parameters or body
        action = query_parameters.get('action') or body.get('action')
        
        # Route based on HTTP method and action
        if http_method == 'POST' and action == 'create':
            result = create_session(user_id, cognito_session_id)
            return create_cors_response(200 if result['success'] else 500, result)
            
        elif http_method == 'GET' and action == 'list':
            # Check if we should filter by sessions with results
            filter_by_results = query_parameters.get('filter_by_results', '').lower() == 'true'
            result = get_user_sessions(user_id, filter_by_results)
            return create_cors_response(200 if result['success'] else 500, result)
            
        elif http_method == 'PUT' and action == 'update':
            session_id = body.get('session_id')
            title = body.get('title')
            
            if not session_id or not title:
                return create_cors_response(400, {'error': 'session_id and title are required'})
            
            result = update_session_title(session_id, user_id, title)
            return create_cors_response(200 if result['success'] else 500, result)
            
        elif http_method == 'DELETE' and action == 'delete':
            session_id = query_parameters.get('session_id') or body.get('session_id')
            
            if not session_id:
                return create_cors_response(400, {'error': 'session_id is required'})
            
            result = delete_session(session_id, user_id)
            return create_cors_response(200 if result['success'] else 500, result)
            
        elif http_method == 'GET' and action == 'job_status':
            # Check job status for document processing
            job_id = query_parameters.get('job_id')
            
            if not job_id:
                return create_cors_response(400, {'error': 'job_id is required'})
            
            result = get_job_status(job_id, user_id)
            return create_cors_response(200 if result['success'] else 500, result)
            
        elif http_method == 'GET' and action == 'session_results':
            # Get analysis results for a specific session
            session_id = query_parameters.get('session_id')
            
            if not session_id:
                return create_cors_response(400, {'error': 'session_id is required'})
            
            result = get_session_analysis_results(session_id, user_id)
            return create_cors_response(200 if result['success'] else 500, result)
            
        elif http_method == 'PUT' and action == 'mark_results':
            # Mark session as having processed results
            session_id = body.get('session_id')
            
            if not session_id:
                return create_cors_response(400, {'error': 'session_id is required'})
            
            result = mark_session_with_results(session_id, user_id)
            return create_cors_response(200 if result['success'] else 500, result)
            
        else:
            return create_cors_response(404, {'error': 'Action not found'})
            
    except Exception as e:
        logger.error(f"Unexpected error in session management: {e}")
        return create_cors_response(500, {'error': 'Internal server error'})