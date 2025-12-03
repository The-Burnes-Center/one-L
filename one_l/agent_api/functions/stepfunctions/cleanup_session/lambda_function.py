"""
Cleanup session Lambda function.
Wraps existing _cleanup_session_documents function.
"""

import json
import boto3
import logging
from agent_api.agent.prompts.models import CleanupOutput
from agent_api.agent.tools import _cleanup_session_documents

logger = logging.getLogger()
logger.setLevel(logging.INFO)

def lambda_handler(event, context):
    """
    Clean up temporary session documents.
    
    Args:
        event: Lambda event with session_id, user_id
        context: Lambda context
        
    Returns:
        CleanupOutput with success and message
    """
    try:
        session_id = event.get('session_id')
        user_id = event.get('user_id')
        
        # Handle missing session_id or user_id gracefully
        # This can happen in edge cases, but we should still attempt cleanup if possible
        if not session_id or not user_id:
            logger.warning(f"Cleanup called with missing context: session_id={session_id}, user_id={user_id}")
            # Return success=False but don't fail - cleanup is best-effort
            output = CleanupOutput(
                success=False,
                message=f"Cleanup skipped: session_id and user_id are required (got session_id={session_id}, user_id={user_id})"
            )
            return output.model_dump()
        
        # Call cleanup function
        logger.info(f"Cleaning up session {session_id} for user {user_id}")
        cleanup_result = _cleanup_session_documents(session_id, user_id)
        
        # Check if cleanup was successful
        if cleanup_result.get('success'):
            output = CleanupOutput(
                success=True,
                message=f"Session {session_id} cleaned up successfully: {cleanup_result.get('message', '')}"
            )
        else:
            # Log warning but still return success=False
            logger.warning(f"Cleanup completed with issues for session {session_id}: {cleanup_result.get('error', 'Unknown error')}")
            output = CleanupOutput(
                success=False,
                message=f"Cleanup completed with issues: {cleanup_result.get('error', 'Unknown error')}"
            )
        
        # Return plain result
        return output.model_dump()
        
    except Exception as e:
        logger.error(f"Error in cleanup_session: {e}")
        output = CleanupOutput(
            success=False,
            message=f"Cleanup failed: {str(e)}"
        )
        return output.model_dump()

