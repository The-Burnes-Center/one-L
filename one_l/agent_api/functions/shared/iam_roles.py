"""
Shared IAM roles for Lambda functions.
"""

from constructs import Construct
from aws_cdk import (
    aws_iam as iam,
    aws_s3 as s3,
    aws_opensearchservice as opensearch,
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