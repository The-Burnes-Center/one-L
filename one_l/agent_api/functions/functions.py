"""
Lambda Functions construct for Agent API operations.
"""

from typing import Optional
from constructs import Construct
from aws_cdk import (
    aws_s3 as s3,
    aws_opensearchserverless as aoss,
    Stack
)
from .shared.iam_roles import IAMRolesConstruct
from .knowledge_management.knowledge_management import KnowledgeManagementConstruct
from .agent.agent import AgentConstruct


class FunctionsConstruct(Construct):
    """
    Functions construct that orchestrates various function categories.
    """
    
    def __init__(
        self, 
        scope: Construct, 
        construct_id: str,
        knowledge_bucket: s3.Bucket,
        user_documents_bucket: s3.Bucket,
        agent_processing_bucket: s3.Bucket,  # Add agent_processing_bucket parameter
        knowledge_base_id: str,
        opensearch_collection: aoss.CfnCollection,
        **kwargs
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)
        
        # Store bucket and collection references
        self.knowledge_bucket = knowledge_bucket
        self.user_documents_bucket = user_documents_bucket
        self.agent_processing_bucket = agent_processing_bucket  # Store agent processing bucket
        self.knowledge_base_id = knowledge_base_id
        self.opensearch_collection = opensearch_collection
        
        # Get storage construct from parent to access additional resources
        self.parent_storage = None
        
        # Configuration
        self._stack_name = Stack.of(self).stack_name
        
        # Create shared IAM roles
        self.iam_roles = IAMRolesConstruct(self, "IAMRoles")
        
        # Create function categories
        self.knowledge_management = KnowledgeManagementConstruct(
            self, "KnowledgeManagement",
            knowledge_bucket=knowledge_bucket,
            user_documents_bucket=user_documents_bucket,
            agent_processing_bucket=agent_processing_bucket,  # Pass agent processing bucket
            knowledge_base_id=knowledge_base_id,
            opensearch_collection=opensearch_collection,
            iam_roles=self.iam_roles
        )
        
        # Create agent functions (will be updated with storage references)
        self.agent = None
    
    def get_function_routes(self) -> dict:
        """
        Get function routing metadata for API Gateway.
        
        Returns a dictionary defining available functions and their routing configurations.
        This allows the API Gateway to be completely generic and not tied to specific function types.
        """
        
        routes = {
            "knowledge_management": self.knowledge_management.get_function_routes()
        }
        
        # Add agent routes if available
        if self.agent:
            routes["agent"] = self.agent.get_function_routes()
            
        return routes
    
    def setup_agent_with_storage(self, storage_construct):
        """Setup agent construct with complete storage references."""
        self.parent_storage = storage_construct
        
        # Create agent construct with all required resources
        self.agent = AgentConstruct(
            self, "Agent",
            knowledge_bucket=self.knowledge_bucket,
            user_documents_bucket=self.user_documents_bucket,
            agent_processing_bucket=storage_construct.agent_processing_bucket,
            analysis_table=storage_construct.analysis_table,
            opensearch_collection=self.opensearch_collection,
            knowledge_base_id=self.knowledge_base_id,
            iam_roles=self.iam_roles
        ) 