"""
Agent functions construct for AI-powered document review operations.
"""

import os
from typing import Optional
from constructs import Construct
from aws_cdk import (
    aws_lambda as _lambda,
    aws_iam as iam,
    aws_s3 as s3,
    aws_dynamodb as dynamodb,
    aws_opensearchserverless as aoss,
    aws_logs as logs,
    Duration,
    Stack
)

# Google Document AI configuration removed - reverted to PyMuPDF-based conversion
# Google Document AI code saved in tools_pymupdf_conversion_backup.py for future reference
# try:
#     import sys
#     sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../../../'))
#     from constants import (
#         GOOGLE_CLOUD_PROJECT_ID,
#         GOOGLE_DOCUMENT_AI_PROCESSOR_ID,
#         GOOGLE_DOCUMENT_AI_LOCATION
#     )
# except ImportError:
#     GOOGLE_CLOUD_PROJECT_ID = ""
#     GOOGLE_DOCUMENT_AI_PROCESSOR_ID = ""
#     GOOGLE_DOCUMENT_AI_LOCATION = "us"


class AgentConstruct(Construct):
    """
    Agent construct that pieces together individual Lambda functions for AI-powered document review.
    Follows the same pattern as KnowledgeManagementConstruct.
    """
    
    def __init__(
        self, 
        scope: Construct, 
        construct_id: str,
        knowledge_bucket: s3.Bucket,
        user_documents_bucket: s3.Bucket,
        agent_processing_bucket: s3.Bucket,
        analysis_table: dynamodb.Table,
        opensearch_collection: aoss.CfnCollection,
        knowledge_base_id: str,
        iam_roles,
        **kwargs
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)
        
        # Store references
        self.knowledge_bucket = knowledge_bucket
        self.user_documents_bucket = user_documents_bucket
        self.agent_processing_bucket = agent_processing_bucket
        self.analysis_table = analysis_table
        self.opensearch_collection = opensearch_collection
        self.knowledge_base_id = knowledge_base_id
        self.buckets = [knowledge_bucket, user_documents_bucket, agent_processing_bucket]
        self.iam_roles = iam_roles
        
        # Configuration
        self._stack_name = Stack.of(self).stack_name
        
        # Instance variables for Lambda functions
        self.document_review_function = None
        
        # Create agent functions
        self.create_functions()
    
    def create_functions(self):
        """Create agent Lambda functions."""
        self.create_document_review_function()
    
    def create_document_review_function(self):
        """Create Lambda function for AI-powered document review."""
        
        # Create role with comprehensive permissions
        role = self.iam_roles.create_agent_role(
            "DocumentReview", 
            self.buckets, 
            self.analysis_table,
            self.opensearch_collection
        )
        
        # Create Lambda function using pre-compiled wheels for lxml to avoid compilation issues
        self.document_review_function = _lambda.Function(
            self, "DocumentReviewFunction",
            function_name=f"{self._stack_name}-document-review",
            runtime=_lambda.Runtime.PYTHON_3_12,
            handler="lambda_function.lambda_handler",
            code=_lambda.Code.from_asset("build/lambda-deployment.zip"),
            role=role,
            timeout=Duration.minutes(15),  # Long timeout for AI processing
            memory_size=2048,
            log_retention=logs.RetentionDays.ONE_WEEK,
            environment={
                "KNOWLEDGE_BUCKET": self.knowledge_bucket.bucket_name,
                "USER_DOCUMENTS_BUCKET": self.user_documents_bucket.bucket_name,
                "AGENT_PROCESSING_BUCKET": self.agent_processing_bucket.bucket_name,
                "ANALYSIS_TABLE": self.analysis_table.table_name,
                "ANALYSIS_RESULTS_TABLE": self.analysis_table.table_name,  # Add this for job status tracking
                "KNOWLEDGE_BASE_ID": self.knowledge_base_id,
                "OPENSEARCH_COLLECTION_ENDPOINT": f"{self.opensearch_collection.attr_id}.{Stack.of(self).region}.aoss.amazonaws.com",
                "REGION": Stack.of(self).region,
                "LOG_LEVEL": "INFO",
                # Enable OCR fallback for PDFs in dev to handle scanned/flattened documents
                "ENABLE_TEXTRACT_OCR": "1",
                # Google Document AI configuration removed - reverted to PyMuPDF-based conversion
                # Google Document AI code saved in tools_pymupdf_conversion_backup.py for future reference
            }
        )
    
    def get_function_routes(self) -> dict:
        """
        Get function routing metadata for API Gateway.
        
        Returns a dictionary defining available functions and their routing configurations.
        """
        
        return {
            "review": {
                "function": self.document_review_function,
                "path": "review",
                "methods": ["POST"],
                "description": "AI-powered document review with conflict detection"
            }
        } 