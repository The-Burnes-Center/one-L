"""
AWS CDK Construct for S3 Storage System.
"""

from typing import Optional
from constructs import Construct
from aws_cdk import (
    aws_s3 as s3,
    RemovalPolicy,
    Stack,
    CfnOutput
)


class StorageConstruct(Construct):
    """
    Storage construct using AWS S3.
    Provides knowledge source bucket and user documents bucket.
    """
    
    def __init__(
        self, 
        scope: Construct, 
        construct_id: str,
        knowledge_bucket_name: Optional[str] = None,
        user_documents_bucket_name: Optional[str] = None,
        **kwargs
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)
        
        # Instance variables - store bucket references
        self.knowledge_bucket = None
        self.user_documents_bucket = None
        
        # Configuration - ensure all names start with stack name
        self._stack_name = Stack.of(self).stack_name
        self._knowledge_bucket_name = knowledge_bucket_name or f"{self._stack_name.lower()}-knowledge-source"
        self._user_documents_bucket_name = user_documents_bucket_name or f"{self._stack_name.lower()}-user-documents"
        
        # Create the storage infrastructure
        self.create_knowledge_bucket()
        self.create_user_documents_bucket()
        self.create_outputs()
    
    def create_knowledge_bucket(self):
        """Create the knowledge source bucket."""
        self.knowledge_bucket = s3.Bucket(
            self, "KnowledgeSourceBucket",
            bucket_name=self._knowledge_bucket_name,
            versioned=True,
            encryption=s3.BucketEncryption.S3_MANAGED,
            enforce_ssl=True,
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
            cors=[
                s3.CorsRule(
                    allowed_methods=[
                        s3.HttpMethods.GET,
                        s3.HttpMethods.POST,
                        s3.HttpMethods.PUT,
                        s3.HttpMethods.DELETE,
                    ],
                    allowed_origins=["*"],
                    allowed_headers=["*"],
                )
            ],
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
        )
    
    def create_user_documents_bucket(self):
        """Create the user documents upload bucket."""
        self.user_documents_bucket = s3.Bucket(
            self, "UserDocumentsBucket",
            bucket_name=self._user_documents_bucket_name,
            versioned=True,
            encryption=s3.BucketEncryption.S3_MANAGED,
            enforce_ssl=True,
            removal_policy=RemovalPolicy.DESTROY,  # Since documents might be purged
            auto_delete_objects=True,  # Allows purging
            cors=[
                s3.CorsRule(
                    allowed_methods=[
                        s3.HttpMethods.GET,
                        s3.HttpMethods.HEAD,
                        s3.HttpMethods.POST,
                        s3.HttpMethods.PUT,
                        s3.HttpMethods.DELETE
                    ],
                    allowed_origins=["http://localhost:3000"],
                    allowed_headers=["*"],
                    exposed_headers=["ETag"],
                    max_age=3000,
                )
            ],
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
        )
    
    def create_outputs(self):
        """Create CloudFormation outputs."""
        CfnOutput(
            self, "KnowledgeBucketName",
            value=self.knowledge_bucket.bucket_name,
            description="S3 Knowledge Source Bucket Name",
            export_name=f"{self._stack_name}-KnowledgeBucketName"
        )
        
        CfnOutput(
            self, "UserDocumentsBucketName",
            value=self.user_documents_bucket.bucket_name,
            description="S3 User Documents Bucket Name",
            export_name=f"{self._stack_name}-UserDocumentsBucketName"
        )
        
        CfnOutput(
            self, "KnowledgeBucketArn",
            value=self.knowledge_bucket.bucket_arn,
            description="S3 Knowledge Source Bucket ARN",
            export_name=f"{self._stack_name}-KnowledgeBucketArn"
        )
        
        CfnOutput(
            self, "UserDocumentsBucketArn",
            value=self.user_documents_bucket.bucket_arn,
            description="S3 User Documents Bucket ARN",
            export_name=f"{self._stack_name}-UserDocumentsBucketArn"
        )
    
    def grant_read_access(self, principal, bucket_name: str = "both"):
        """Grant read access to specified bucket(s)."""
        if bucket_name in ["knowledge", "both"]:
            self.knowledge_bucket.grant_read(principal)
        
        if bucket_name in ["user_documents", "both"]:
            self.user_documents_bucket.grant_read(principal)
    
    def grant_write_access(self, principal, bucket_name: str = "both"):
        """Grant write access to specified bucket(s)."""
        if bucket_name in ["knowledge", "both"]:
            self.knowledge_bucket.grant_write(principal)
        
        if bucket_name in ["user_documents", "both"]:
            self.user_documents_bucket.grant_write(principal)
    
    def grant_read_write_access(self, principal, bucket_name: str = "both"):
        """Grant read/write access to specified bucket(s)."""
        if bucket_name in ["knowledge", "both"]:
            self.knowledge_bucket.grant_read_write(principal)
        
        if bucket_name in ["user_documents", "both"]:
            self.user_documents_bucket.grant_read_write(principal) 