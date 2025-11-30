"""
User Interface construct for React app hosting with CloudFront.
"""

import json
from typing import Optional
from constructs import Construct
from aws_cdk import (
    aws_s3 as s3,
    aws_cloudfront as cloudfront,
    aws_cloudfront_origins as origins,
    aws_s3_deployment as s3_deployment,
    aws_lambda as _lambda,
    aws_logs as logs,
    aws_iam as iam,
    RemovalPolicy,
    Stack,
    CfnOutput,
    Duration,
    CustomResource,
    custom_resources as cr
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
        agent_api_construct=None,
        **kwargs
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)
        
        # Store references to other constructs
        self.authorization = authorization_construct
        self.api_gateway = api_gateway_construct
        self.agent_api = agent_api_construct
        
        # Instance variables
        self.website_bucket = None
        self.cloudfront_distribution = None
        self.config_generator_function = None
        
        # Configuration
        self._stack_name = Stack.of(self).stack_name
        
        # Create the user interface infrastructure
        self.create_website_bucket()
        self.create_cloudfront_distribution()
        self.create_config_generator()
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
        
        # Create CloudFront distribution using S3BucketOrigin with Origin Access Control (OAC)
        # This is the modern approach replacing S3Origin with OAI
        self.cloudfront_distribution = cloudfront.Distribution(
            self, "WebsiteDistribution",
            default_root_object="index.html",
            default_behavior=cloudfront.BehaviorOptions(
                origin=origins.S3BucketOrigin.with_origin_access_control(
                    self.website_bucket
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
        
        # Update Cognito settings
        self.create_frontend_config()
        
        # Deploy the built React app to S3
        website_deployment = s3_deployment.BucketDeployment(
            self, "WebsiteDeployment",
            sources=[s3_deployment.Source.asset("one_l/user_interface/build")],
            destination_bucket=self.website_bucket,
            distribution=self.cloudfront_distribution,
            distribution_paths=["/*"],
            retain_on_delete=False,
        )
        
        # Create Custom Resource to trigger config generation after deployment
        config_trigger = cr.AwsCustomResource(
            self, "ConfigGeneratorTrigger",
            on_create=cr.AwsSdkCall(
                service="Lambda",
                action="invoke",
                parameters={
                    "FunctionName": self.config_generator_function.function_name,
                    "Payload": json.dumps({"action": "generate"})
                },
                physical_resource_id=cr.PhysicalResourceId.of("ConfigGeneratorTrigger")
            ),
            on_update=cr.AwsSdkCall(
                service="Lambda",
                action="invoke",
                parameters={
                    "FunctionName": self.config_generator_function.function_name,
                    "Payload": json.dumps({"action": "generate"})
                },
                physical_resource_id=cr.PhysicalResourceId.of("ConfigGeneratorTrigger")
            ),
            policy=cr.AwsCustomResourcePolicy.from_statements([
                iam.PolicyStatement(
                    effect=iam.Effect.ALLOW,
                    actions=["lambda:InvokeFunction"],
                    resources=[self.config_generator_function.function_arn]
                )
            ])
        )
        
        # Ensure config generation happens after all dependencies are created
        config_trigger.node.add_dependency(self.config_generator_function)
        config_trigger.node.add_dependency(self.api_gateway.main_api)
        config_trigger.node.add_dependency(self.authorization.user_pool)
        config_trigger.node.add_dependency(self.authorization.user_pool_client)
        config_trigger.node.add_dependency(self.authorization.user_pool_domain)
    
    def create_frontend_config(self):
        """Update Cognito settings for frontend deployment."""
        
        # Update Cognito callback URLs with CloudFront URL
        if self.authorization:
            cloudfront_url = f"https://{self.cloudfront_distribution.distribution_domain_name}"
            self.update_cognito_callback_urls(cloudfront_url)
    
    def create_config_generator(self):
        """Create Lambda function to generate config.json post-deployment."""
        
        if not self.authorization or not self.api_gateway:
            raise ValueError("Authorization and API Gateway constructs are required for config generation")
        
        # Import the shared IAM roles construct
        from ..agent_api.functions.shared.iam_roles import IAMRolesConstruct
        iam_roles = IAMRolesConstruct(self, "ConfigIAMRoles")
        
        # Create IAM role for config generator Lambda
        config_role = iam_roles.create_website_config_role("ConfigGenerator", self.website_bucket)
        
        # Create Lambda function for config generation
        self.config_generator_function = _lambda.Function(
            self, "ConfigGeneratorFunction",
            function_name=f"{self._stack_name}-config-generator",
            runtime=_lambda.Runtime.PYTHON_3_9,
            handler="generate_config_lambda.lambda_handler",
            code=_lambda.Code.from_asset("one_l/user_interface/config"),
            role=config_role,
            timeout=Duration.seconds(60),
            memory_size=512,
            # Keep using log_retention (deprecated but stable) to avoid creating new LogGroup resources
            log_retention=logs.RetentionDays.ONE_WEEK,
            environment={
                "WEBSITE_BUCKET": self.website_bucket.bucket_name,
                "API_GATEWAY_URL": self.api_gateway.main_api.url,
                "USER_POOL_ID": self.authorization.user_pool.user_pool_id,
                "USER_POOL_CLIENT_ID": self.authorization.user_pool_client.user_pool_client_id,
                "USER_POOL_DOMAIN": f"https://{self.authorization.user_pool_domain.domain_name}.auth.{Stack.of(self).region}.amazoncognito.com",
                "REGION": Stack.of(self).region,
                "STACK_NAME": self._stack_name,
                "WEBSOCKET_URL": self.agent_api.get_websocket_api_url() if self.agent_api else "",
                "LOG_LEVEL": "INFO"
            }
        )
    
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
        
        # Website Bucket Name
        CfnOutput(
            self, "WebsiteBucketName",
            value=self.website_bucket.bucket_name,
            description="S3 bucket name for website",
            export_name=f"{self._stack_name}-WebsiteBucketName"
        )
        
        # CloudFront Distribution ID
        CfnOutput(
            self, "CloudFrontDistributionId",
            value=self.cloudfront_distribution.distribution_id,
            description="CloudFront Distribution ID",
            export_name=f"{self._stack_name}-CloudFrontDistributionId"
        )
        
        # CloudFront Domain Name
        CfnOutput(
            self, "CloudFrontDomainName",
            value=self.cloudfront_distribution.distribution_domain_name,
            description="CloudFront Distribution Domain Name",
            export_name=f"{self._stack_name}-CloudFrontDomainName"
        )
        
        # Website URL
        CfnOutput(
            self, "WebsiteUrl",
            value=f"https://{self.cloudfront_distribution.distribution_domain_name}",
            description="Website URL (CloudFront)",
            export_name=f"{self._stack_name}-WebsiteUrl"
        )
 