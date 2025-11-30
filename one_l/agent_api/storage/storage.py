"""
AWS CDK Construct for S3 Storage System.
"""

from typing import Optional
from constructs import Construct
from aws_cdk import (
    aws_s3 as s3,
    aws_dynamodb as dynamodb,
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
        additional_cors_origins: Optional[list] = None,
        **kwargs
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)
        
        # Instance variables - store bucket and table references
        self.knowledge_bucket = None
        self.user_documents_bucket = None
        self.agent_processing_bucket = None
        self.analysis_table = None
        self.sessions_table = None
        
        # Configuration - ensure all names start with stack name
        self._stack_name = Stack.of(self).stack_name
        self._knowledge_bucket_name = knowledge_bucket_name or f"{self._stack_name.lower()}-knowledge-source"
        self._user_documents_bucket_name = user_documents_bucket_name or f"{self._stack_name.lower()}-user-documents"
        self._agent_processing_bucket_name = f"{self._stack_name.lower()}-agent-processing"
        self._analysis_table_name = f"{self._stack_name}-analysis-results"
        self._sessions_table_name = f"{self._stack_name}-sessions"
        self._additional_cors_origins = additional_cors_origins or []
        
        # Create the storage infrastructure
        self.create_knowledge_bucket()
        self.create_user_documents_bucket()
        self.create_agent_processing_bucket()
        self.create_analysis_table()
        self.create_sessions_table()
        self.create_outputs()
    
    def create_knowledge_bucket(self):
        """Create the knowledge source bucket."""
        
        # Build CORS allowed origins list  
        cors_origins = [
            "http://localhost:3000",    # Local development
            "https://localhost:3000",   # Local development with HTTPS
        ]
        cors_origins.extend(self._additional_cors_origins)
        
        # If no additional origins provided, allow all origins for CloudFront compatibility
        if not self._additional_cors_origins:
            cors_origins = ["*"]
        
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
                    allowed_origins=cors_origins,
                    allowed_headers=["*"],
                )
            ],
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
        )
    
    def create_user_documents_bucket(self):
        """Create the user documents upload bucket."""
        
        # Build CORS allowed origins list
        cors_origins = [
            "http://localhost:3000",    # Local development
            "https://localhost:3000",   # Local development with HTTPS
        ]
        cors_origins.extend(self._additional_cors_origins)
        
        # If no additional origins provided, allow all origins for CloudFront compatibility
        if not self._additional_cors_origins:
            cors_origins = ["*"]
        
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
                    allowed_origins=cors_origins,
                    allowed_headers=["*"],
                    exposed_headers=["ETag"],
                    max_age=3000,
                )
            ],
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
        )
    
    def create_agent_processing_bucket(self):
        """Create the agent processing bucket for input/output documents."""
        
        # Build CORS allowed origins list
        cors_origins = [
            "http://localhost:3000",    # Local development
            "https://localhost:3000",   # Local development with HTTPS
        ]
        cors_origins.extend(self._additional_cors_origins)
        
        # If no additional origins provided, allow all origins for CloudFront compatibility
        if not self._additional_cors_origins:
            cors_origins = ["*"]
        
        self.agent_processing_bucket = s3.Bucket(
            self, "AgentProcessingBucket",
            bucket_name=self._agent_processing_bucket_name,
            versioned=True,
            encryption=s3.BucketEncryption.S3_MANAGED,
            enforce_ssl=True,
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
            cors=[
                s3.CorsRule(
                    allowed_methods=[
                        s3.HttpMethods.GET,
                        s3.HttpMethods.HEAD,  # Add HEAD method for preflight checks
                        s3.HttpMethods.POST,
                        s3.HttpMethods.PUT,
                        s3.HttpMethods.DELETE,
                    ],
                    allowed_origins=cors_origins,
                    allowed_headers=["*"],
                    exposed_headers=["ETag"],  # Add ETag header for upload verification
                    max_age=3000,  # Add max_age for CORS preflight caching
                )
            ],
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
        )
    
    def create_analysis_table(self):
        """Create DynamoDB table for storing analysis results."""
        
        self.analysis_table = dynamodb.Table(
            self, "AnalysisTable",
            table_name=self._analysis_table_name,
            partition_key=dynamodb.Attribute(
                name="analysis_id",
                type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name="timestamp",
                type=dynamodb.AttributeType.STRING
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.DESTROY,
            point_in_time_recovery_specification=dynamodb.PointInTimeRecoverySpecification(
                point_in_time_recovery_enabled=True
            )
        )
        
        # Add GSI for querying by document
        self.analysis_table.add_global_secondary_index(
            index_name="document-index",
            partition_key=dynamodb.Attribute(
                name="document_s3_key",
                type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name="timestamp",
                type=dynamodb.AttributeType.STRING
            )
        )
    
    def create_sessions_table(self):
        """Create DynamoDB table for storing user sessions."""
        
        self.sessions_table = dynamodb.Table(
            self, "SessionsTable",
            table_name=self._sessions_table_name,
            partition_key=dynamodb.Attribute(
                name="session_id",
                type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name="user_id",
                type=dynamodb.AttributeType.STRING
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.DESTROY
        )
        
        # Add GSI for querying by user_id
        self.sessions_table.add_global_secondary_index(
            index_name="user-index",
            partition_key=dynamodb.Attribute(
                name="user_id",
                type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name="created_at",
                type=dynamodb.AttributeType.STRING
            )
        )
    
    def create_outputs(self):
        """Create CloudFormation outputs."""
        pass
    
    def grant_read_access(self, principal, bucket_name: str = "all"):
        """Grant read access to specified bucket(s)."""
        if bucket_name in ["knowledge", "all"]:
            self.knowledge_bucket.grant_read(principal)
        
        if bucket_name in ["user_documents", "all"]:
            self.user_documents_bucket.grant_read(principal)
        
        if bucket_name in ["agent_processing", "all"]:
            self.agent_processing_bucket.grant_read(principal)
    
    def grant_write_access(self, principal, bucket_name: str = "all"):
        """Grant write access to specified bucket(s)."""
        if bucket_name in ["knowledge", "all"]:
            self.knowledge_bucket.grant_write(principal)
        
        if bucket_name in ["user_documents", "all"]:
            self.user_documents_bucket.grant_write(principal)
        
        if bucket_name in ["agent_processing", "all"]:
            self.agent_processing_bucket.grant_write(principal)
    
    def grant_read_write_access(self, principal, bucket_name: str = "all"):
        """Grant read/write access to specified bucket(s)."""
        if bucket_name in ["knowledge", "all"]:
            self.knowledge_bucket.grant_read_write(principal)
        
        if bucket_name in ["user_documents", "all"]:
            self.user_documents_bucket.grant_read_write(principal)
        
        if bucket_name in ["agent_processing", "all"]:
            self.agent_processing_bucket.grant_read_write(principal)
    
    def grant_dynamodb_access(self, principal, table_name: str = "analysis"):
        """Grant DynamoDB access to specified table(s)."""
        if table_name in ["analysis", "all"]:
            self.analysis_table.grant_read_write_data(principal)
        if table_name in ["sessions", "all"]:
            if self.sessions_table:
                self.sessions_table.grant_read_write_data(principal) 