"""
User Interface construct for React app hosting with CloudFront.
"""

from typing import Optional
from constructs import Construct
from aws_cdk import (
    aws_s3 as s3,
    aws_cloudfront as cloudfront,
    aws_cloudfront_origins as origins,
    aws_s3_deployment as s3_deployment,
    aws_iam as iam,
    RemovalPolicy,
    Stack,
    CfnOutput,
    Duration
)
import json


class UserInterfaceConstruct(Construct):
    """
    User Interface construct for hosting React app with CloudFront.
    """
    
    def __init__(
        self, 
        scope: Construct, 
        construct_id: str,
        authorization_construct=None,
        api_gateway_construct=None,
        **kwargs
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)
        
        # Store references to other constructs
        self.authorization = authorization_construct
        self.api_gateway = api_gateway_construct
        
        # Instance variables
        self.website_bucket = None
        self.cloudfront_distribution = None
        
        # Configuration
        self._stack_name = Stack.of(self).stack_name
        
        # Create the user interface infrastructure
        self.create_website_bucket()
        self.create_cloudfront_distribution()
        self.deploy_website()
        self.create_outputs()
    
    def create_website_bucket(self):
        """Create S3 bucket for static website hosting."""
        self.website_bucket = s3.Bucket(
            self, "WebsiteBucket",
            bucket_name=f"{self._stack_name.lower()}-website",
            versioned=False,
            encryption=s3.BucketEncryption.S3_MANAGED,
            enforce_ssl=True,
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
        )
    
    def create_cloudfront_distribution(self):
        """Create CloudFront distribution for the website."""
        
        # Create Origin Access Identity for CloudFront (legacy but reliable approach)
        origin_access_identity = cloudfront.OriginAccessIdentity(
            self, "WebsiteOAI",
            comment=f"OAI for {self._stack_name} website"
        )
        
        # Grant read permissions to CloudFront
        self.website_bucket.grant_read(origin_access_identity)
        
        # Create CloudFront distribution
        self.cloudfront_distribution = cloudfront.Distribution(
            self, "WebsiteDistribution",
            default_root_object="index.html",
            default_behavior=cloudfront.BehaviorOptions(
                origin=origins.S3Origin(
                    bucket=self.website_bucket,
                    origin_access_identity=origin_access_identity
                ),
                viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                allowed_methods=cloudfront.AllowedMethods.ALLOW_GET_HEAD_OPTIONS,
                cached_methods=cloudfront.CachedMethods.CACHE_GET_HEAD_OPTIONS,
                compress=True,
                cache_policy=cloudfront.CachePolicy.CACHING_OPTIMIZED,
            ),
            error_responses=[
                cloudfront.ErrorResponse(
                    http_status=404,
                    response_http_status=200,
                    response_page_path="/index.html",
                    ttl=Duration.minutes(5)
                ),
                cloudfront.ErrorResponse(
                    http_status=403,
                    response_http_status=200,
                    response_page_path="/index.html",
                    ttl=Duration.minutes(5)
                )
            ],
            price_class=cloudfront.PriceClass.PRICE_CLASS_100,
            comment=f"CloudFront distribution for {self._stack_name} website"
        )
    
    def deploy_website(self):
        """Deploy the React app to S3."""
        
        # Configure frontend deployment settings
        self.create_frontend_config()
        
        # Deploy the built React app to S3
        s3_deployment.BucketDeployment(
            self, "WebsiteDeployment",
            sources=[s3_deployment.Source.asset("one_l/user_interface/build")],
            destination_bucket=self.website_bucket,
            distribution=self.cloudfront_distribution,
            distribution_paths=["/*"],
            retain_on_delete=False,
        )
    

    def create_frontend_config(self):
        """Configure frontend deployment settings."""
        
        # Update Cognito callback URLs with CloudFront URL
        if self.authorization:
            cloudfront_url = f"https://{self.cloudfront_distribution.distribution_domain_name}"
            self.update_cognito_callback_urls(cloudfront_url)
    
    def update_cognito_callback_urls(self, cloudfront_url: str):
        """Update Cognito user pool client with CloudFront callback URLs."""
        cfn_client = self.authorization.user_pool_client.node.default_child
        cfn_client.callback_ur_ls = [
            cloudfront_url,
            "http://localhost:3000"  # For local development
        ]
        cfn_client.logout_ur_ls = [
            cloudfront_url,
            "http://localhost:3000"  # For local development
        ]
    
    def create_outputs(self):
        """Create CloudFormation outputs."""
        
        # Website URL
        CfnOutput(
            self, "WebsiteUrl",
            value=f"https://{self.cloudfront_distribution.distribution_domain_name}",
            description="Website URL (CloudFront)",
            export_name=f"{self._stack_name}-WebsiteUrl"
        )
        
        # CloudFront Distribution ID
        CfnOutput(
            self, "CloudFrontDistributionId",
            value=self.cloudfront_distribution.distribution_id,
            description="CloudFront Distribution ID",
            export_name=f"{self._stack_name}-CloudFrontDistributionId"
        )
        
        # S3 Bucket Name
        CfnOutput(
            self, "WebsiteBucketName",
            value=self.website_bucket.bucket_name,
            description="S3 Website Bucket Name",
            export_name=f"{self._stack_name}-WebsiteBucketName"
        ) 