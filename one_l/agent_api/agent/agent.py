"""
Main Agent class for Legal-AI document review.
Encapsulates model and tools following composition design pattern.
"""

import logging
from typing import Dict, Any
from .model import Model
from .tools import redline_document, save_analysis_to_dynamodb, clear_knowledge_base_cache

logger = logging.getLogger()
logger.setLevel(logging.INFO)


class Agent:
    """
    Main Agent class that encapsulates all document review functionality.
    Follows composition design pattern by coordinating between Model and tools.
    """
    
    def __init__(self, knowledge_base_id: str, region: str):
        """
        Initialize the Agent with required configuration.
        
        Args:
            knowledge_base_id: AWS Bedrock Knowledge Base ID
            region: AWS region
        """
        self.knowledge_base_id = knowledge_base_id
        self.region = region
        
        # Clear cache when Agent is initialized to prevent cross-KB cache pollution
        # This ensures each document review starts with a fresh cache for the specific KB
        clear_knowledge_base_cache()
        logger.info(f"Cache cleared for new Agent instance with KB: {knowledge_base_id}")
        
        self._model = Model(knowledge_base_id, region)
        
        logger.info(f"Agent initialized with knowledge base: {knowledge_base_id}")
    
    def create_redlined_document(self, analysis_data: str, document_s3_key: str, bucket_type: str = "user_documents", session_id: str = None, user_id: str = None) -> Dict[str, Any]:
        """
        Create a redlined version of the document with conflicts highlighted.
        
        Args:
            analysis_data: Analysis text containing conflict information 
            document_s3_key: S3 key of the original document
            bucket_type: Type of source bucket (user_documents, knowledge, agent_processing)
            session_id: Session ID for organizing output files
            user_id: User ID for organizing output files
            
        Returns:
            Dictionary containing redlined document information
        """
        try:
            logger.info(f"Creating redlined document for document: {document_s3_key}, session: {session_id}")
            
            # Delegate to the redlining tool - it handles all document operations
            result = redline_document(analysis_data, document_s3_key, bucket_type, session_id, user_id)
            
            logger.info(f"Redlined document creation completed: {result.get('success', False)}")
            return result
            
        except Exception as e:
            logger.error(f"Error in agent redlined document creation: {str(e)}")
            return {
                "success": False,
                "error": str(e),
                "original_document": document_s3_key
            }
    
    def get_knowledge_base_id(self) -> str:
        """Get the knowledge base ID."""
        return self.knowledge_base_id
    
    def get_region(self) -> str:
        """Get the AWS region."""
        return self.region 