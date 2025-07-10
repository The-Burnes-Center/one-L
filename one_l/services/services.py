"""
Services construct that combines all service-level constructs.
"""

from typing import Optional
from constructs import Construct
from storage.storage import StorageConstruct


class ServicesConstruct(Construct):
    """
    Services construct that combines all service-level constructs.
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
        
        # Create service constructs
        self.create_storage()
    
    def create_storage(self):
        """Create the storage construct."""
        self.storage = StorageConstruct(
            self, "Storage",
            knowledge_bucket_name="onel-knowledge-source",
            user_documents_bucket_name="onel-user-documents"
        )
