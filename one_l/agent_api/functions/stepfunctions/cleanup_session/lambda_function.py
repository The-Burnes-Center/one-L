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
        
        if not session_id or not user_id:
            raise ValueError("session_id and user_id are required")
        
        # Call cleanup function
        logger.info(f"Cleaning up session {session_id}")
        _cleanup_session_documents(session_id, user_id)
        
        output = CleanupOutput(
            success=True,
            message=f"Session {session_id} cleaned up successfully"
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

