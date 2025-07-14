"""
Knowledge Management functions construct for S3 operations.
"""

from typing import Optional
from constructs import Construct
from aws_cdk import (
    aws_lambda as _lambda,
    aws_s3 as s3,
    aws_logs as logs,
    Duration,
    Stack
)


class KnowledgeManagementConstruct(Construct):
    """
    Knowledge Management construct that creates Lambda functions for S3 operations.
    """
    
    def __init__(
        self, 
        scope: Construct, 
        construct_id: str,
        knowledge_bucket: s3.Bucket,
        user_documents_bucket: s3.Bucket,
        iam_roles,
        **kwargs
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)
        
        # Store references
        self.knowledge_bucket = knowledge_bucket
        self.user_documents_bucket = user_documents_bucket
        self.buckets = [knowledge_bucket, user_documents_bucket]
        self.iam_roles = iam_roles
        
        # Configuration
        self._stack_name = Stack.of(self).stack_name
        
        # Instance variables for Lambda functions
        self.upload_to_s3_function = None
        self.retrieve_from_s3_function = None
        self.delete_from_s3_function = None
        
        # Create Lambda functions
        self.create_functions()
    
    def create_functions(self):
        """Create all knowledge management Lambda functions."""
        self.create_upload_to_s3_function()
        self.create_retrieve_from_s3_function()
        self.create_delete_from_s3_function()
    
    def create_upload_to_s3_function(self):
        """Create Lambda function for uploading files to S3."""
        
        # Create role using shared IAM construct
        role = self.iam_roles.create_s3_write_role("UploadToS3", self.buckets)
        
        # Create Lambda function
        self.upload_to_s3_function = _lambda.Function(
            self, "UploadToS3Function",
            function_name=f"{self._stack_name}-upload-to-s3",
            runtime=_lambda.Runtime.PYTHON_3_9,
            handler="lambda_function.lambda_handler",
            code=_lambda.Code.from_asset("one_l/agent_api/functions/knowledge_management/upload_to_s3"),
            role=role,
            timeout=Duration.seconds(60),
            memory_size=512,
            environment={
                "KNOWLEDGE_BUCKET": self.knowledge_bucket.bucket_name,
                "USER_DOCUMENTS_BUCKET": self.user_documents_bucket.bucket_name,
                "LOG_LEVEL": "INFO"
            },
            log_retention=logs.RetentionDays.ONE_WEEK
        )
    
    def create_retrieve_from_s3_function(self):
        """Create Lambda function for retrieving files from S3."""
        
        # Create role using shared IAM construct
        role = self.iam_roles.create_s3_read_role("RetrieveFromS3", self.buckets)
        
        # Create Lambda function
        self.retrieve_from_s3_function = _lambda.Function(
            self, "RetrieveFromS3Function",
            function_name=f"{self._stack_name}-retrieve-from-s3",
            runtime=_lambda.Runtime.PYTHON_3_9,
            handler="lambda_function.lambda_handler",
            code=_lambda.Code.from_asset("one_l/agent_api/functions/knowledge_management/retrieve_from_s3"),
            role=role,
            timeout=Duration.seconds(60),
            memory_size=512,
            environment={
                "KNOWLEDGE_BUCKET": self.knowledge_bucket.bucket_name,
                "USER_DOCUMENTS_BUCKET": self.user_documents_bucket.bucket_name,
                "LOG_LEVEL": "INFO"
            },
            log_retention=logs.RetentionDays.ONE_WEEK
        )
    
    def create_delete_from_s3_function(self):
        """Create Lambda function for deleting files from S3."""
        
        # Create role using shared IAM construct
        role = self.iam_roles.create_s3_delete_role("DeleteFromS3", self.buckets)
        
        # Create Lambda function
        self.delete_from_s3_function = _lambda.Function(
            self, "DeleteFromS3Function",
            function_name=f"{self._stack_name}-delete-from-s3",
            runtime=_lambda.Runtime.PYTHON_3_9,
            handler="lambda_function.lambda_handler",
            code=_lambda.Code.from_asset("one_l/agent_api/functions/knowledge_management/delete_from_s3"),
            role=role,
            timeout=Duration.seconds(60),
            memory_size=512,
            environment={
                "KNOWLEDGE_BUCKET": self.knowledge_bucket.bucket_name,
                "USER_DOCUMENTS_BUCKET": self.user_documents_bucket.bucket_name,
                "LOG_LEVEL": "INFO"
            },
            log_retention=logs.RetentionDays.ONE_WEEK
        )
    
    def get_function_routes(self) -> dict:
        """
        Get function routing metadata for this knowledge management category.
        
        Returns routing configuration for all knowledge management functions.
        """
        
        return {
            "upload": {
                "function": self.upload_to_s3_function,
                "methods": ["POST"],
                "path": "upload",
                "description": "Upload files to S3"
            },
            "retrieve": {
                "function": self.retrieve_from_s3_function,
                "methods": ["POST"],
                "path": "retrieve",
                "description": "Retrieve files from S3"
            },
            "delete": {
                "function": self.delete_from_s3_function,
                "methods": ["DELETE"],
                "path": "delete",
                "description": "Delete files from S3"
            }
        } 