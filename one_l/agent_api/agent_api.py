"""
Agent API construct that combines all service-level constructs.
"""

from typing import Optional
from constructs import Construct
from aws_cdk import Stack, aws_iam as iam
from .storage.storage import StorageConstruct
from .opensearch.opensearch import OpenSearchConstruct
from .knowledge_base.knowledge_base import KnowledgeBaseConstruct
from .functions.functions import FunctionsConstruct
from .agent.agent import Agent


class AgentApiConstruct(Construct):
    """
    Agent API construct that combines all service-level constructs.
    """
    
    def __init__(
        self, 
        scope: Construct, 
        construct_id: str,
        authorization=None,  # Optional: Authorization construct for Cognito access
        **kwargs
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)
        
        # Instance variables for service constructs
        self.storage = None
        self.opensearch = None
        self.knowledge_base = None
        self.functions = None
        self.knowledge_base_role = None
        self.authorization = authorization  # Store authorization construct
        
        # Instance variable for agent business logic
        self.agent = None
        
        # Configuration - ensure all names start with stack name
        self._stack_name = Stack.of(self).stack_name
        
        # Create service constructs in proper dependency order
        self.create_storage()
        self.create_knowledge_base_role()
        self.create_opensearch()
        self.create_functions()  # This now creates the index
        self.create_knowledge_base()  # This now depends on index creation
        self.create_agent()  # Create agent business logic after knowledge base is ready
    
    def create_storage(self):
        """Create the storage construct."""
        self.storage = StorageConstruct(
            self, "Storage",
            knowledge_bucket_name=f"{self._stack_name.lower()}-knowledge-source",
            user_documents_bucket_name=f"{self._stack_name.lower()}-user-documents"
        )
    
    def create_opensearch(self):
        """Create the OpenSearch Serverless construct."""
        self.opensearch = OpenSearchConstruct(
            self, "OpenSearch",
            collection_name=f"{self._stack_name.lower()}-vector-db",
            knowledge_base_role_arn=self.knowledge_base_role.role_arn
        )
    
    def create_knowledge_base_role(self):
        """Create IAM role for Knowledge Base service."""
        self.knowledge_base_role = iam.Role(
            self, "KnowledgeBaseRole",
            role_name=f"{self._stack_name}-kb-role",
            assumed_by=iam.ServicePrincipal("bedrock.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name("AmazonBedrockFullAccess")
            ]
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
    
    def create_functions(self):
        """Create the functions construct."""
        self.functions = FunctionsConstruct(
            self, "Functions",
            knowledge_bucket=self.storage.knowledge_bucket,
            user_documents_bucket=self.storage.user_documents_bucket,
            agent_processing_bucket=self.storage.agent_processing_bucket,  # Pass agent processing bucket directly
            knowledge_base_id="placeholder",  # Will be updated after knowledge base creation
            opensearch_collection=self.opensearch.collection,
            authorization=self.authorization  # Pass authorization construct for Cognito access
        )
        
        # Setup agent functions with complete storage references
        self.functions.setup_agent_with_storage(self.storage)
    
    def create_knowledge_base(self):
        """Create the Knowledge Base construct."""
        self.knowledge_base = KnowledgeBaseConstruct(
            self, "KnowledgeBase",
            opensearch_collection=self.opensearch.collection,
            knowledge_bucket=self.storage.knowledge_bucket,
            user_documents_bucket=self.storage.user_documents_bucket,
            knowledge_base_role=self.knowledge_base_role,
            vector_index_dependency=self.functions.knowledge_management.get_create_index_dependency()
        )
        
        # Update the knowledge base ID in functions after creation
        # Note: This creates a circular dependency that CDK can handle
        self.functions.knowledge_management.sync_knowledge_base_function.add_environment(
            "KNOWLEDGE_BASE_ID", 
            self.knowledge_base.get_knowledge_base_id()
        )
        
        # Update the knowledge base ID in agent functions after creation
        if hasattr(self.functions, 'agent') and self.functions.agent:
            self.functions.agent.document_review_function.add_environment(
                "KNOWLEDGE_BASE_ID", 
                self.knowledge_base.get_knowledge_base_id()
            )
            
            # Also update Step Functions Lambda functions if enabled
            if hasattr(self.functions.agent, 'stepfunctions_construct') and self.functions.agent.stepfunctions_construct:
                self.functions.agent.stepfunctions_construct.update_knowledge_base_id(
                    self.knowledge_base.get_knowledge_base_id()
                )
    
    def create_agent(self):
        """Create the Agent business logic following composition pattern."""
        if self.knowledge_base:
            # Initialize the agent with the knowledge base ID and region
            # Note: This is the business logic layer, not the infrastructure
            knowledge_base_id = self.knowledge_base.get_knowledge_base_id()
            region = Stack.of(self).region
            
            # The Agent business logic will be available for runtime operations
            # This follows the composition pattern used throughout the project
            self.agent = Agent(knowledge_base_id, region)
    
    def get_agent(self) -> Optional[Agent]:
        """Get the agent business logic instance."""
        return self.agent
    
    def get_agent_functions(self):
        """Get the agent functions construct."""
        return self.functions.agent if hasattr(self.functions, 'agent') else None
    
    def get_websocket_construct(self):
        """Get the WebSocket construct for real-time communication."""
        return self.functions.websocket if hasattr(self.functions, 'websocket') else None
    
    def get_websocket_api_url(self) -> str:
        """Get the WebSocket API URL."""
        if hasattr(self.functions, 'websocket') and self.functions.websocket:
            return self.functions.websocket.get_websocket_api_url()
        return None