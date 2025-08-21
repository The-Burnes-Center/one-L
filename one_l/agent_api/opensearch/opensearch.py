"""
AWS CDK Construct for OpenSearch Serverless Collection.
"""

from typing import Optional
import json
from constructs import Construct
from aws_cdk import (
    aws_opensearchserverless as aoss,
    aws_iam as iam,
    RemovalPolicy,
    Stack,
    CfnOutput
)


class OpenSearchConstruct(Construct):
    """
    OpenSearch Serverless construct for vector search.
    Provides managed vector database for Knowledge Base operations.
    """
    
    def __init__(
        self, 
        scope: Construct, 
        construct_id: str,
        collection_name: Optional[str] = None,
        knowledge_base_role_arn: Optional[str] = None,
        **kwargs
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)
        
        # Instance variables - store collection reference
        self.collection = None
        self.vector_index_name = "knowledge-base-index"
        
        # Configuration - ensure all names start with stack name
        self._stack_name = Stack.of(self).stack_name
        self._collection_name = collection_name or f"{self._stack_name.lower()}-vector-db"
        self._knowledge_base_role_arn = knowledge_base_role_arn
        
        # Create the OpenSearch Serverless infrastructure
        self.create_encryption_policy()
        self.create_network_policy()
        self.create_access_policy()
        self.create_collection()
        self.create_outputs()
    
    def create_encryption_policy(self):
        """Create encryption policy for the collection."""
        self.encryption_policy = aoss.CfnSecurityPolicy(
            self, "EncryptionPolicy",
            name=f"{self._collection_name}-enc-policy",
            type="encryption",
            policy=f'''{{
                "Rules": [
                    {{
                        "ResourceType": "collection",
                        "Resource": ["collection/{self._collection_name}"]
                    }}
                ],
                "AWSOwnedKey": true
            }}'''
        )
    
    def create_network_policy(self):
        """Create network policy for the collection."""
        self.network_policy = aoss.CfnSecurityPolicy(
            self, "NetworkPolicy",
            name=f"{self._collection_name}-net-policy",
            type="network",
            policy=f'''[
                {{
                    "Rules": [
                        {{
                            "ResourceType": "collection",
                            "Resource": ["collection/{self._collection_name}"]
                        }},
                        {{
                            "ResourceType": "dashboard",
                            "Resource": ["collection/{self._collection_name}"]
                        }}
                    ],
                    "AllowFromPublic": true
                }}
            ]'''
        )
    
    def create_access_policy(self):
        """Create access policy for the collection."""
        # Get current account ID for the policy
        account_id = Stack.of(self).account
        
        # Build principals list
        principals = [f"arn:aws:iam::{account_id}:root"]
        if self._knowledge_base_role_arn:
            principals.append(self._knowledge_base_role_arn)
        
        # Convert principals list to JSON array string
        principals_json = ', '.join(f'"{principal}"' for principal in principals)
        
        self.access_policy = aoss.CfnAccessPolicy(
            self, "AccessPolicy",
            name=f"{self._collection_name}-acc-policy",
            type="data",
            policy=f'''[
                {{
                    "Rules": [
                        {{
                            "ResourceType": "collection",
                            "Resource": ["collection/{self._collection_name}"],
                            "Permission": [
                                "aoss:CreateCollectionItems",
                                "aoss:DeleteCollectionItems", 
                                "aoss:UpdateCollectionItems",
                                "aoss:DescribeCollectionItems"
                            ]
                        }},
                        {{
                            "ResourceType": "index",
                            "Resource": ["index/{self._collection_name}/*"],
                            "Permission": [
                                "aoss:CreateIndex",
                                "aoss:DeleteIndex",
                                "aoss:UpdateIndex",
                                "aoss:DescribeIndex",
                                "aoss:ReadDocument",
                                "aoss:WriteDocument"
                            ]
                        }}
                    ],
                    "Principal": [
                        {principals_json}
                    ]
                }}
            ]'''
        )
    
    def create_collection(self):
        """Create OpenSearch Serverless collection for vector search."""
        
        # Create the collection
        self.collection = aoss.CfnCollection(
            self, "VectorCollection",
            name=self._collection_name,
            type="VECTORSEARCH",
            description=f"Vector search collection for {self._stack_name} Knowledge Base"
        )
        
        # Add dependencies to ensure policies are created first
        self.collection.add_dependency(self.encryption_policy)
        self.collection.add_dependency(self.network_policy) 
        self.collection.add_dependency(self.access_policy)
    
    def create_outputs(self):
        """Create CloudFormation outputs."""
        pass
    
    def get_collection_endpoint(self) -> str:
        """Get the OpenSearch Serverless collection endpoint."""
        return self.collection.attr_collection_endpoint
    
    def get_collection_arn(self) -> str:
        """Get the OpenSearch Serverless collection ARN."""
        return self.collection.attr_arn
    
    def get_collection_name(self) -> str:
        """Get the OpenSearch Serverless collection name."""
        return self.collection.name
    
    def get_vector_index_name(self) -> str:
        """Get the vector index name."""
        return self.vector_index_name
    
    def grant_read_access(self, principal):
        """Grant read access to the collection (handled by access policy)."""
        pass  # Access handled by collection-level policies
    
    def grant_write_access(self, principal):
        """Grant write access to the collection (handled by access policy)."""
        pass  # Access handled by collection-level policies
    
    def grant_read_write_access(self, principal):
        """Grant read and write access to the collection (handled by access policy).""" 
        pass  # Access handled by collection-level policies 