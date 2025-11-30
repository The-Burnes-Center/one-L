"""
AWS CDK Construct for Bedrock Knowledge Base Service.
"""

from typing import Optional
from constructs import Construct
from aws_cdk import (
    aws_bedrock as bedrock,
    aws_opensearchserverless as aoss,
    aws_s3 as s3,
    aws_iam as iam,
    RemovalPolicy,
    Stack,
    CfnOutput
)


class KnowledgeBaseConstruct(Construct):
    """
    Knowledge Base construct that creates AWS Bedrock Knowledge Base.
    Provides a fully managed RAG system with Titan embeddings and OpenSearch Serverless.
    """
    
    def __init__(
        self, 
        scope: Construct, 
        construct_id: str,
        opensearch_collection: aoss.CfnCollection,
        knowledge_bucket: s3.Bucket,
        user_documents_bucket: s3.Bucket,
        knowledge_base_role: iam.Role,
        vector_index_dependency=None,
        **kwargs
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)
        
        # Store references to infrastructure components
        self.opensearch_collection = opensearch_collection
        self.knowledge_bucket = knowledge_bucket
        self.user_documents_bucket = user_documents_bucket
        self.knowledge_base_role = knowledge_base_role
        self.vector_index_dependency = vector_index_dependency
        
        # Configuration
        self._stack_name = Stack.of(self).stack_name
        self._kb_name = f"{self._stack_name}-knowledge-base"
        
        # Instance variables
        self.knowledge_base = None
        self.knowledge_data_source = None
        self.user_documents_data_source = None
        
        # Create knowledge base infrastructure
        self.configure_knowledge_base_role()
        self.create_knowledge_base()
        self.create_data_sources()
        self.create_outputs()
    
    def configure_knowledge_base_role(self):
        """Configure the existing IAM role for Knowledge Base service."""
        
        # Grant access to S3 buckets
        self.knowledge_bucket.grant_read(self.knowledge_base_role)
        self.user_documents_bucket.grant_read(self.knowledge_base_role)
        
        # Grant access to OpenSearch Serverless collection
        # Construct ARN manually to avoid early validation issues with attr_arn
        collection_arn = f"arn:aws:aoss:{Stack.of(self).region}:{Stack.of(self).account}:collection/{self.opensearch_collection.attr_id}"
        
        self.knowledge_base_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=[
                    "aoss:APIAccessAll",
                    "aoss:CreateIndex",
                    "aoss:DeleteIndex",
                    "aoss:UpdateIndex",
                    "aoss:DescribeIndex",
                    "aoss:ReadDocument",
                    "aoss:WriteDocument",
                    "aoss:CreateCollectionItems",
                    "aoss:DeleteCollectionItems",
                    "aoss:UpdateCollectionItems",
                    "aoss:DescribeCollectionItems"
                ],
                resources=[
                    collection_arn,
                    f"{collection_arn}/*"
                ]
            )
        )
        
        # Grant access to Titan embedding model
        self.knowledge_base_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=[
                    "bedrock:InvokeModel"
                ],
                resources=[
                    f"arn:aws:bedrock:{Stack.of(self).region}::foundation-model/amazon.titan-embed-text-v2:0"
                ]
            )
        )
    
    def create_knowledge_base(self):
        """Create the Bedrock Knowledge Base."""
        
        self.knowledge_base = bedrock.CfnKnowledgeBase(
            self, "KnowledgeBase",
            name=self._kb_name,
            description=f"Knowledge Base for {self._stack_name} with Titan embeddings and OpenSearch Serverless",
            role_arn=self.knowledge_base_role.role_arn,
            
            # Knowledge base configuration
            knowledge_base_configuration=bedrock.CfnKnowledgeBase.KnowledgeBaseConfigurationProperty(
                type="VECTOR",
                vector_knowledge_base_configuration=bedrock.CfnKnowledgeBase.VectorKnowledgeBaseConfigurationProperty(
                    embedding_model_arn=f"arn:aws:bedrock:{Stack.of(self).region}::foundation-model/amazon.titan-embed-text-v2:0"
                )
            ),
            
            # Storage configuration for OpenSearch Serverless
            storage_configuration=bedrock.CfnKnowledgeBase.StorageConfigurationProperty(
                type="OPENSEARCH_SERVERLESS",
                opensearch_serverless_configuration=bedrock.CfnKnowledgeBase.OpenSearchServerlessConfigurationProperty(
                    collection_arn=self.opensearch_collection.attr_arn,
                    vector_index_name="knowledge-base-index",
                    field_mapping=bedrock.CfnKnowledgeBase.OpenSearchServerlessFieldMappingProperty(
                        vector_field="vector_field",
                        text_field="text_field",
                        metadata_field="metadata_field"
                    )
                )
            )
        )
        
        # Ensure Knowledge Base is created after the collection
        self.knowledge_base.add_dependency(self.opensearch_collection)
        
        # Ensure Knowledge Base is created after the vector index is ready
        if self.vector_index_dependency:
            self.knowledge_base.node.add_dependency(self.vector_index_dependency)
    
    def create_data_sources(self):
        """Create S3 data sources for both existing S3 buckets."""
        
        # Data source for knowledge bucket (reference documents)
        self.knowledge_data_source = bedrock.CfnDataSource(
            self, "KnowledgeS3DataSource",
            name=f"{self._stack_name}-knowledge-data-source",
            description="Knowledge bucket data source for reference documents",
            knowledge_base_id=self.knowledge_base.attr_knowledge_base_id,
            
            # Data source configuration for knowledge bucket
            data_source_configuration=bedrock.CfnDataSource.DataSourceConfigurationProperty(
                type="S3",
                s3_configuration=bedrock.CfnDataSource.S3DataSourceConfigurationProperty(
                    bucket_arn=self.knowledge_bucket.bucket_arn,
                    bucket_owner_account_id=Stack.of(self).account
                )
            ),
            
            # Vector ingestion configuration
            vector_ingestion_configuration=bedrock.CfnDataSource.VectorIngestionConfigurationProperty(
                chunking_configuration=bedrock.CfnDataSource.ChunkingConfigurationProperty(
                    chunking_strategy="FIXED_SIZE",
                    fixed_size_chunking_configuration=bedrock.CfnDataSource.FixedSizeChunkingConfigurationProperty(
                        max_tokens=300,
                        overlap_percentage=20
                    )
                )
            )
        )
        
        # Data source for user documents bucket (user uploads)
        self.user_documents_data_source = bedrock.CfnDataSource(
            self, "UserDocumentsS3DataSource",
            name=f"{self._stack_name}-user-documents-data-source",
            description="User documents bucket data source for uploaded files",
            knowledge_base_id=self.knowledge_base.attr_knowledge_base_id,
            
            # Data source configuration for user documents bucket
            data_source_configuration=bedrock.CfnDataSource.DataSourceConfigurationProperty(
                type="S3",
                s3_configuration=bedrock.CfnDataSource.S3DataSourceConfigurationProperty(
                    bucket_arn=self.user_documents_bucket.bucket_arn,
                    bucket_owner_account_id=Stack.of(self).account
                )
            ),
            
            # Vector ingestion configuration
            vector_ingestion_configuration=bedrock.CfnDataSource.VectorIngestionConfigurationProperty(
                chunking_configuration=bedrock.CfnDataSource.ChunkingConfigurationProperty(
                    chunking_strategy="FIXED_SIZE",
                    fixed_size_chunking_configuration=bedrock.CfnDataSource.FixedSizeChunkingConfigurationProperty(
                        max_tokens=300,
                        overlap_percentage=20
                    )
                )
            )
        )
        
        # Ensure data sources are created after Knowledge Base
        self.knowledge_data_source.add_dependency(self.knowledge_base)
        self.user_documents_data_source.add_dependency(self.knowledge_base)
    
    def create_outputs(self):
        """Create CloudFormation outputs."""
        pass
    
    def get_knowledge_base_id(self) -> str:
        """Get the Knowledge Base ID."""
        return self.knowledge_base.attr_knowledge_base_id
    
    def get_knowledge_base_arn(self) -> str:
        """Get the Knowledge Base ARN."""
        return self.knowledge_base.attr_knowledge_base_arn
    
    def get_knowledge_data_source_id(self) -> str:
        """Get the Knowledge Bucket Data Source ID."""
        return self.knowledge_data_source.attr_data_source_id
    
    def get_user_documents_data_source_id(self) -> str:
        """Get the User Documents Bucket Data Source ID."""
        return self.user_documents_data_source.attr_data_source_id 