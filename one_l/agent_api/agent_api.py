"""
Agent API construct that combines all service-level constructs.
"""

from typing import Optional
from constructs import Construct
from aws_cdk import Stack
from .storage.storage import StorageConstruct
from .functions.functions import FunctionsConstruct


class AgentApiConstruct(Construct):
    """
    Agent API construct that combines all service-level constructs.
    """
    
    def __init__(
        self, 
        scope: Construct, 
        construct_id: str,
        **kwargs
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)
        
        # Instance variables for service constructs
        self.storage = None
        self.functions = None
        
        # Configuration - ensure all names start with stack name
        self._stack_name = Stack.of(self).stack_name
        
        # Create service constructs
        self.create_storage()
        self.create_functions()
    
    def create_storage(self):
        """Create the storage construct."""
        self.storage = StorageConstruct(
            self, "Storage",
            knowledge_bucket_name=f"{self._stack_name.lower()}-knowledge-source",
            user_documents_bucket_name=f"{self._stack_name.lower()}-user-documents"
        )
    
    def create_functions(self):
        """Create the functions construct."""
        self.functions = FunctionsConstruct(
            self, "Functions",
            knowledge_bucket=self.storage.knowledge_bucket,
            user_documents_bucket=self.storage.user_documents_bucket
        )
