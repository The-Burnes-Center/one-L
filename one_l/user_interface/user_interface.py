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
    RemovalPolicy,
    Stack,
    CfnOutput,
    Duration
)


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
        
        # Create Origin Access Identity for CloudFront
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
                    self.website_bucket,
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
        
        # Create environment configuration for the frontend
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
        """Create configuration file for the frontend with API endpoints."""
        
        # Generate configuration based on other constructs
        config_data = {
            "apiGatewayUrl": self.api_gateway.main_api.url if self.api_gateway else "",
            "userPoolId": self.authorization.user_pool.user_pool_id if self.authorization else "",
            "userPoolClientId": self.authorization.user_pool_client.user_pool_client_id if self.authorization else "",
            "userPoolDomain": self.authorization.user_pool_domain.domain_name if self.authorization else "",
            "region": Stack.of(self).region,
            "stackName": self._stack_name
        }
        
        # Add specific function endpoint URLs from API Gateway
        if self.api_gateway and self.api_gateway.functions:
            function_definitions = self.api_gateway.functions.get_function_routes()
            
            for category, functions in function_definitions.items():
                for func_name, func_config in functions.items():
                    # Create endpoint URL
                    endpoint_url = f"{self.api_gateway.main_api.url}{category}/{func_config['path']}"
                    
                    # Create config key (e.g., knowledgeManagementUploadEndpointUrl)
                    config_key = f"{category}{''.join(word.capitalize() for word in func_name.split('_'))}EndpointUrl"
                    
                    config_data[config_key] = endpoint_url
        
        # Write config to a file that will be included in the build
        import json
        import os
        
        # Ensure the build directory exists
        build_dir = "one_l/user_interface/build"
        os.makedirs(build_dir, exist_ok=True)
        
        # Write configuration
        with open(f"{build_dir}/config.json", "w") as f:
            json.dump(config_data, f, indent=2)
    
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