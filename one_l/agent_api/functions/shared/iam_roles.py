"""
Shared IAM roles for Lambda functions.
"""

from constructs import Construct
from aws_cdk import (
    aws_iam as iam,
    aws_s3 as s3,
    aws_opensearchservice as opensearch,
    aws_dynamodb as dynamodb,
)


class IAMRolesConstruct(Construct):
    """
    Shared IAM roles for Lambda functions.
    """
    
    def __init__(
        self, 
        scope: Construct, 
        construct_id: str,
        **kwargs
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)
    
    def create_s3_read_role(self, role_name: str, buckets: list[s3.Bucket]) -> iam.Role:
        """Create IAM role with S3 read permissions."""
        role = iam.Role(
            self, f"{role_name}ReadRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AWSLambdaBasicExecutionRole"
                )
            ]
        )
        
        # Grant read access to specified buckets
        for bucket in buckets:
            bucket.grant_read(role)
        
        return role
    
    def create_s3_write_role(self, role_name: str, buckets: list[s3.Bucket]) -> iam.Role:
        """Create IAM role with S3 write permissions."""
        role = iam.Role(
            self, f"{role_name}WriteRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AWSLambdaBasicExecutionRole"
                )
            ]
        )
        
        # Grant write access to specified buckets
        for bucket in buckets:
            bucket.grant_write(role)
        
        return role
    
    def create_s3_delete_role(self, role_name: str, buckets: list[s3.Bucket]) -> iam.Role:
        """Create IAM role with S3 delete permissions."""
        role = iam.Role(
            self, f"{role_name}DeleteRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AWSLambdaBasicExecutionRole"
                )
            ]
        )
        
        # Grant delete access to specified buckets
        for bucket in buckets:
            bucket.grant_delete(role)
        
        return role
    
    def create_s3_read_write_role(self, role_name: str, buckets: list[s3.Bucket]) -> iam.Role:
        """Create IAM role with S3 read and write permissions."""
        role = iam.Role(
            self, f"{role_name}ReadWriteRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AWSLambdaBasicExecutionRole"
                )
            ]
        )
        
        # Grant read/write access to specified buckets
        for bucket in buckets:
            bucket.grant_read_write(role)
        
        return role
    
    def create_website_config_role(self, role_name: str, website_bucket: s3.Bucket) -> iam.Role:
        """Create IAM role for configuration Lambda to write to website bucket."""
        role = iam.Role(
            self, f"{role_name}WebsiteConfigRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AWSLambdaBasicExecutionRole"
                )
            ]
        )
        
        # Grant write access to website bucket for config.json
        website_bucket.grant_write(role)
        
        return role
    
    def create_opensearch_read_role(self, role_name: str, opensearch_domain: opensearch.Domain) -> iam.Role:
        """Create IAM role with OpenSearch read permissions."""
        role = iam.Role(
            self, f"{role_name}OpenSearchReadRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AWSLambdaBasicExecutionRole"
                )
            ]
        )
        
        # Grant read access to OpenSearch domain
        opensearch_domain.grant_read(role)
        
        return role
    
    def create_opensearch_write_role(self, role_name: str, opensearch_domain: opensearch.Domain) -> iam.Role:
        """Create IAM role with OpenSearch write permissions."""
        role = iam.Role(
            self, f"{role_name}OpenSearchWriteRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AWSLambdaBasicExecutionRole"
                )
            ]
        )
        
        # Grant write access to OpenSearch domain
        opensearch_domain.grant_write(role)
        
        return role
    
    def create_opensearch_read_write_role(self, role_name: str, opensearch_domain: opensearch.Domain) -> iam.Role:
        """Create IAM role with OpenSearch read and write permissions."""
        role = iam.Role(
            self, f"{role_name}OpenSearchReadWriteRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AWSLambdaBasicExecutionRole"
                )
            ]
        )
        
        # Grant read/write access to OpenSearch domain
        opensearch_domain.grant_read_write(role)
        
        return role
    
    def create_s3_opensearch_role(self, role_name: str, buckets: list[s3.Bucket], opensearch_domain: opensearch.Domain) -> iam.Role:
        """Create IAM role with both S3 and OpenSearch permissions."""
        role = iam.Role(
            self, f"{role_name}S3OpenSearchRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AWSLambdaBasicExecutionRole"
                )
            ]
        )
        
        # Grant access to S3 buckets
        for bucket in buckets:
            bucket.grant_read_write(role)
        
        # Grant access to OpenSearch domain
        opensearch_domain.grant_read_write(role)
        
        return role
    
    def create_agent_role(self, role_name: str, buckets: list, analysis_table, opensearch_collection) -> iam.Role:
        """Create IAM role with all necessary permissions for agent operations."""
        
        role = iam.Role(
            self, f"{role_name}AgentRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AWSLambdaBasicExecutionRole"
                )
            ]
        )
        
        # Grant S3 access to all buckets
        for bucket in buckets:
            bucket.grant_read_write(role)
        
        # Grant DynamoDB access
        analysis_table.grant_read_write_data(role)
        
        # Grant Bedrock permissions for Claude Sonnet 4 with Converse API
        role.add_to_policy(
            iam.PolicyStatement(
                sid="AllowClaudeSonnet4Inference",
                effect=iam.Effect.ALLOW,
                actions=[
                    "bedrock:InvokeModel",
                    "bedrock:InvokeModelWithResponseStream",
                    "bedrock:Converse",
                    "bedrock:ConverseStream"
                ],
                resources=[
                    # Inference profile with account ID
                    f"arn:aws:bedrock:*:*:inference-profile/us.anthropic.claude-sonnet-4-20250514-v1:0",
                    # Foundation model as fallback
                    f"arn:aws:bedrock:*::foundation-model/anthropic.claude-sonnet-4-20250514-v1:0"
                ]
            )
        )

        
        
        # Grant Knowledge Base permissions
        role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=[
                    "bedrock:Retrieve",
                    "bedrock:RetrieveAndGenerate", 
                    "bedrock:GetKnowledgeBase",
                    "bedrock:ListKnowledgeBases"
                ],
                resources=[
                    "arn:aws:bedrock:*:*:knowledge-base/*"
                ]
            )
        )
        
        # Grant OpenSearch Serverless permissions
        role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=[
                    "aoss:APIAccessAll"
                ],
                resources=[
                    opensearch_collection.attr_arn
                ]
            )
        )
        
        # Grant Lambda invoke permissions for WebSocket notifications, session management, and cleanup operations
        role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=[
                    "lambda:InvokeFunction"
                ],
                resources=[
                    f"arn:aws:lambda:*:*:function:*-websocket-notification",
                    f"arn:aws:lambda:*:*:function:*-session-management",
                    f"arn:aws:lambda:*:*:function:*-delete-from-s3",
                    f"arn:aws:lambda:*:*:function:*-sync-knowledge-base"
                ]
            )
        )
        
        return role
    
    def create_websocket_role(self, role_name: str, connections_table: dynamodb.Table) -> iam.Role:
        """Create IAM role for WebSocket Lambda functions."""
        role = iam.Role(
            self, f"{role_name}WebSocketRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AWSLambdaBasicExecutionRole"
                )
            ]
        )
        
        # Grant DynamoDB permissions for connections table
        connections_table.grant_read_write_data(role)
        
        # Grant API Gateway Management permissions for posting to connections
        role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=[
                    "execute-api:ManageConnections"
                ],
                resources=["*"]  # WebSocket API ARN pattern is complex, using * for simplicity
            )
        )
        
        return role 