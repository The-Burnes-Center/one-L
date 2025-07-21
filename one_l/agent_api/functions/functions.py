"""
Lambda Functions construct for Agent API operations.
"""

from typing import Optional
from constructs import Construct
from aws_cdk import (
    aws_s3 as s3,
    Stack
)
from .shared.iam_roles import IAMRolesConstruct
from .knowledge_management.knowledge_management import KnowledgeManagementConstruct


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
        **kwargs
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)
        
        # Store bucket references
        self.knowledge_bucket = knowledge_bucket
        self.user_documents_bucket = user_documents_bucket
        
        # Configuration
        self._stack_name = Stack.of(self).stack_name
        
        # Create shared IAM roles
        self.iam_roles = IAMRolesConstruct(self, "IAMRoles")
        
        # Create function categories
        self.knowledge_management = KnowledgeManagementConstruct(
            self, "KnowledgeManagement",
            knowledge_bucket=knowledge_bucket,
            user_documents_bucket=user_documents_bucket,
            iam_roles=self.iam_roles
        )
    
    def get_function_routes(self) -> dict:
        """
        Get function routing metadata for API Gateway.
        
        Returns a dictionary defining available functions and their routing configurations.
        This allows the API Gateway to be completely generic and not tied to specific function types.
        """
        
        return {
            "knowledge_management": self.knowledge_management.get_function_routes()
        } 