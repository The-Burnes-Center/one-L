"""
Knowledge Management functions construct for S3 operations.
"""

from typing import Optional
from constructs import Construct
from aws_cdk import (
    aws_lambda as _lambda,
    aws_s3 as s3,
    aws_s3_notifications as s3n,
    aws_logs as logs,
    aws_opensearchserverless as aoss,
    custom_resources as cr,
    aws_iam as iam,
    Duration,
    Stack,
    CustomResource
)


class KnowledgeManagementConstruct(Construct):
    """
    Knowledge Management construct that creates Lambda functions for S3 operations.
    Note: Knowledge Base service handles automatic syncing, embedding, and indexing.
    """
    
    def __init__(
        self, 
        scope: Construct, 
        construct_id: str,
        knowledge_bucket: s3.Bucket,
        user_documents_bucket: s3.Bucket,
        knowledge_base_id: str,
        opensearch_collection: aoss.CfnCollection,
        iam_roles,
        **kwargs
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)
        
        # Store references
        self.knowledge_bucket = knowledge_bucket
        self.user_documents_bucket = user_documents_bucket
        self.knowledge_base_id = knowledge_base_id
        self.opensearch_collection = opensearch_collection
        self.buckets = [knowledge_bucket, user_documents_bucket]
        self.iam_roles = iam_roles
        
        # Configuration
        self._stack_name = Stack.of(self).stack_name
        self._collection_name = f"{self._stack_name.lower()}-vector-db"
        
        # Instance variables for Lambda functions
        self.upload_to_s3_function = None
        self.retrieve_from_s3_function = None
        self.delete_from_s3_function = None
        self.sync_knowledge_base_function = None
        self.create_index_function = None
        self.create_index_provider = None
        self.create_index_custom_resource = None
        
        # Create Lambda functions in proper order
        self.create_functions()
        self.create_index_creation()
        self.setup_s3_event_notifications()
    
    def create_functions(self):
        """Create essential knowledge management Lambda functions."""
        self.create_upload_to_s3_function()
        self.create_retrieve_from_s3_function()
        self.create_delete_from_s3_function()
        self.create_sync_knowledge_base_function()
    
    def create_upload_to_s3_function(self):
        """Create Lambda function for uploading files to S3."""
        
        # Create role with S3 write permissions
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
            memory_size=256,
            environment={
                "KNOWLEDGE_BUCKET": self.knowledge_bucket.bucket_name,
                "USER_DOCUMENTS_BUCKET": self.user_documents_bucket.bucket_name,
                "LOG_LEVEL": "INFO"
            },
            log_retention=logs.RetentionDays.ONE_WEEK
        )
    
    def create_retrieve_from_s3_function(self):
        """Create Lambda function for retrieving files from S3."""
        
        # Create role with S3 read permissions
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
            memory_size=256,
            environment={
                "KNOWLEDGE_BUCKET": self.knowledge_bucket.bucket_name,
                "USER_DOCUMENTS_BUCKET": self.user_documents_bucket.bucket_name,
                "LOG_LEVEL": "INFO"
            },
            log_retention=logs.RetentionDays.ONE_WEEK
        )
    
    def create_delete_from_s3_function(self):
        """Create Lambda function for deleting files from S3."""
        
        # Create role with S3 delete permissions
        role = self.iam_roles.create_s3_write_role("DeleteFromS3", self.buckets)
        
        # Create Lambda function
        self.delete_from_s3_function = _lambda.Function(
            self, "DeleteFromS3Function",
            function_name=f"{self._stack_name}-delete-from-s3",
            runtime=_lambda.Runtime.PYTHON_3_9,
            handler="lambda_function.lambda_handler",
            code=_lambda.Code.from_asset("one_l/agent_api/functions/knowledge_management/delete_from_s3"),
            role=role,
            timeout=Duration.seconds(60),
            memory_size=256,
            environment={
                "KNOWLEDGE_BUCKET": self.knowledge_bucket.bucket_name,
                "USER_DOCUMENTS_BUCKET": self.user_documents_bucket.bucket_name,
                "LOG_LEVEL": "INFO"
            },
            log_retention=logs.RetentionDays.ONE_WEEK
        )
    
    def create_sync_knowledge_base_function(self):
        """Create Lambda function for manually syncing Knowledge Base."""
        
        # Create role with Bedrock and S3 permissions
        role = self.iam_roles.create_s3_read_role("SyncKnowledgeBase", self.buckets)
        
        # Add Bedrock permissions for Knowledge Base sync
        # Use broader permissions since knowledge base ID may not be available at creation time
        role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=[
                    "bedrock:StartIngestionJob",
                    "bedrock:GetIngestionJob",
                    "bedrock:ListIngestionJobs",
                    "bedrock:ListDataSources",
                    "bedrock:GetKnowledgeBase",
                    "bedrock:ListKnowledgeBases"
                ],
                resources=[
                    f"arn:aws:bedrock:{Stack.of(self).region}:{Stack.of(self).account}:knowledge-base/*",
                    f"arn:aws:bedrock:{Stack.of(self).region}:{Stack.of(self).account}:data-source/*"
                ]
            )
        )
        
        # Create Lambda function
        self.sync_knowledge_base_function = _lambda.Function(
            self, "SyncKnowledgeBaseFunction",
            function_name=f"{self._stack_name}-sync-knowledge-base",
            runtime=_lambda.Runtime.PYTHON_3_9,
            handler="lambda_function.lambda_handler",
            code=_lambda.Code.from_asset("one_l/agent_api/functions/knowledge_management/sync_knowledge_base"),
            role=role,
            timeout=Duration.seconds(300),  # Longer timeout for sync operations
            memory_size=512,
            environment={
                "KNOWLEDGE_BASE_ID": self.knowledge_base_id,
                "LOG_LEVEL": "INFO"
            },
            log_retention=logs.RetentionDays.ONE_WEEK
        )
    
    def create_index_creation(self):
        """Create Lambda function and custom resource for OpenSearch vector index creation using proven approach."""
        
        # Create role with OpenSearch permissions (matching working implementation)
        index_function_role = iam.Role(
            self, "IndexFunctionRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AWSLambdaBasicExecutionRole"
                )
            ]
        )
        
        # Add OpenSearch Serverless permissions
        index_function_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=[
                    "aoss:*"
                ],
                resources=[
                    f"arn:aws:aoss:{Stack.of(self).region}:{Stack.of(self).account}:collection/{self.opensearch_collection.attr_id}"
                ]
            )
        )
        
        # Create Lambda function for index creation with bundling for dependencies
        self.create_index_function = _lambda.Function(
            self, "CreateIndexFunction",
            function_name=f"{self._stack_name}-create-index",
            runtime=_lambda.Runtime.PYTHON_3_12,
            handler="lambda_function.lambda_handler",
            code=_lambda.Code.from_asset(
                "one_l/agent_api/functions/knowledge_management/create_index",
                bundling={
                    "image": _lambda.Runtime.PYTHON_3_12.bundling_image,
                    "command": [
                        "bash", "-c",
                        "pip install -r requirements.txt -t /asset-output && cp -au . /asset-output"
                    ]
                }
            ),
            role=index_function_role,
            timeout=Duration.seconds(120),  # Matching working implementation
            memory_size=512,
            environment={
                "COLLECTION_ENDPOINT": f"{self.opensearch_collection.attr_id}.{Stack.of(self).region}.aoss.amazonaws.com",
                "INDEX_NAME": "knowledge-base-index",
                "EMBEDDING_DIM": "1024",
                "REGION": Stack.of(self).region
            },
            log_retention=logs.RetentionDays.ONE_WEEK
        )
        
        # Create custom resource provider (matching working implementation)
        self.create_index_provider = cr.Provider(
            self, "CreateIndexFunctionCustomProvider",
            on_event_handler=self.create_index_function
        )
        
        # Create custom resource to trigger index creation
        self.create_index_custom_resource = CustomResource(
            self, "CreateIndexFunctionCustomResource",
            service_token=self.create_index_provider.service_token
        )
        
        # Ensure index creation happens after OpenSearch collection is ready
        self.create_index_custom_resource.node.add_dependency(self.opensearch_collection)
    
    def setup_s3_event_notifications(self):
        """Set up S3 event notifications to trigger sync on file uploads."""
        if not self.sync_knowledge_base_function:
            raise ValueError("Sync function must be created before setting up S3 events")
        
        # Add S3 event notification to trigger sync when files are uploaded to user documents bucket
        self.user_documents_bucket.add_event_notification(
            s3.EventType.OBJECT_CREATED,
            s3n.LambdaDestination(self.sync_knowledge_base_function),
            s3.NotificationKeyFilter(prefix="uploads/")  # Only trigger for files in uploads/ prefix
        )
        
        # Add permission for S3 to invoke the sync lambda
        self.sync_knowledge_base_function.add_permission(
            "AllowS3Invoke",
            principal=iam.ServicePrincipal("s3.amazonaws.com"),
            action="lambda:InvokeFunction",
            source_arn=self.user_documents_bucket.bucket_arn
        )
    
    def get_function_routes(self) -> dict:
        """
        Get function routing metadata for API Gateway.
        
        Returns a dictionary defining available functions and their routing configurations.
        """
        
        return {
            "upload": {
                "function": self.upload_to_s3_function,
                "path": "upload",
                "methods": ["POST"],
                "description": "Upload files to S3 buckets"
            },
            "retrieve": {
                "function": self.retrieve_from_s3_function,
                "path": "retrieve",
                "methods": ["GET"],
                "description": "Retrieve files from S3 buckets"
            },
            "delete": {
                "function": self.delete_from_s3_function,
                "path": "delete",
                "methods": ["DELETE"],
                "description": "Delete files from S3 buckets"
            },
            "sync": {
                "function": self.sync_knowledge_base_function,
                "path": "sync",
                "methods": ["POST"],
                "description": "Manually sync Knowledge Base with S3 data sources"
            }
        }
    
    def get_create_index_dependency(self):
        """Get the custom resource for index creation to use as a dependency."""
        return self.create_index_custom_resource 