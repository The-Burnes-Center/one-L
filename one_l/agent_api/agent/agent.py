"""
Main Agent class for Legal-AI document review.
Encapsulates model and tools following composition design pattern.
"""

import logging
from typing import Dict, Any
from .model import Model
from .tools import redline_document, save_analysis_to_dynamodb

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
        self._model = Model(knowledge_base_id, region)
        
        logger.info(f"Agent initialized with knowledge base: {knowledge_base_id}")
    
    def review_document(self, bucket_type: str, document_s3_key: str) -> Dict[str, Any]:
        """
        Review a document for conflicts using AI analysis.
        
        Args:
            bucket_type: Type of source bucket
            document_s3_key: S3 key of the document to review
            
        Returns:
            Dictionary containing review results
        """
        try:
            logger.info("Starting document review process")
            
            # Delegate to the model for AI analysis with document attachment
            result = self._model.review_document(bucket_type, document_s3_key)
            
            logger.info(f"Document review completed successfully: {result.get('success', False)}")
            return result
            
        except Exception as e:
            logger.error(f"Error in agent document review: {str(e)}")
            return {
                "success": False,
                "error": str(e),
                "analysis": "",
                "tool_results": [],
                "usage": {},
                "thinking": ""
            }
    
    def create_redlined_document(self, analysis_data: str, document_s3_key: str, bucket_type: str = "user_documents") -> Dict[str, Any]:
        """
        Create a redlined version of the document with conflicts highlighted.
        
        Args:
            analysis_data: Analysis text containing conflict information 
            document_s3_key: S3 key of the original document
            bucket_type: Type of source bucket (user_documents, knowledge, agent_processing)
            
        Returns:
            Dictionary containing redlined document information
        """
        try:
            logger.info(f"Creating redlined document for document: {document_s3_key}")
            
            # Delegate to the redlining tool - it handles all document operations
            result = redline_document(analysis_data, document_s3_key, bucket_type)
            
            logger.info(f"Redlined document creation completed: {result.get('success', False)}")
            return result
            
        except Exception as e:
            logger.error(f"Error in agent redlined document creation: {str(e)}")
            return {
                "success": False,
                "error": str(e),
                "original_document": document_s3_key
            }
    
    def review_and_redline(self, bucket_type: str, document_s3_key: str, analysis_id: str) -> Dict[str, Any]:
        """
        Complete workflow: review document and create redlined version.
        
        Args:
            bucket_type: Type of source bucket
            document_s3_key: S3 key of the original document
            analysis_id: Analysis ID for storing results
            
        Returns:
            Dictionary containing both review and redlining results
        """
        try:
            logger.info("Starting complete review and redline workflow")
            
            # Step 1: Review the document
            review_result = self.review_document(bucket_type, document_s3_key)
            
            if not review_result.get('success', False):
                return {
                    "success": False,
                    "error": f"Review failed: {review_result.get('error', 'Unknown error')}",
                    "review_result": review_result,
                    "redline_result": None
                }
            
            # Step 2: Create redlined document
            redline_result = self.create_redlined_document(analysis_id, document_s3_key)
            
            return {
                "success": True,
                "review_result": review_result,
                "redline_result": redline_result,
                "message": "Document review and redlining completed"
            }
            
        except Exception as e:
            logger.error(f"Error in complete workflow: {str(e)}")
            return {
                "success": False,
                "error": str(e),
                "review_result": None,
                "redline_result": None
            }
    
    def get_knowledge_base_id(self) -> str:
        """Get the knowledge base ID."""
        return self.knowledge_base_id
    
    def get_region(self) -> str:
        """Get the AWS region."""
        return self.region 