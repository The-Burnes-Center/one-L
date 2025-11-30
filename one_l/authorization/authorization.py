"""
AWS CDK Construct for Cognito-based Authorization System.
"""

from typing import Optional
from constructs import Construct
from aws_cdk import (
    aws_cognito as cognito,
    aws_lambda as _lambda,
    aws_iam as iam,
    aws_logs as logs,
    Duration,
    RemovalPolicy,
    Stack,
    CfnOutput
)
from constants import COGNITO_DOMAIN_NAME


class AuthorizationConstruct(Construct):
    """
    Authorization construct using AWS Cognito.
    Provides user pool, user pool client, and authentication lambda.
    """
    
    def __init__(
        self, 
        scope: Construct, 
        construct_id: str,
        user_pool_name: Optional[str] = None,
        **kwargs
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)
        
        # Instance variables
        self.user_pool = None
        self.user_pool_client = None
        self.user_pool_domain = None
        self.auth_lambda = None
        
        # Configuration - ensure all names start with stack name
        self._stack_name = Stack.of(self).stack_name
        self._user_pool_name = user_pool_name or f"{self._stack_name}-user-pool"
        
        # Create the authorization infrastructure
        self.create_user_pool()
        self.create_user_pool_domain()
        self.create_user_pool_client()
        self.create_auth_lambda()
        self.create_outputs()

    def update_callback_urls(self, cloudfront_domain_name: str):
        """Update User Pool Client callback URLs with CloudFront URL."""
        try:
            from aws_cdk import aws_cognito as cognito_cfn
            
            # Get the underlying CloudFormation resource
            cfn_user_pool_client = self.user_pool_client.node.default_child
            
            # Update the callback URLs to include CloudFront domain
            callback_urls = [
                f"https://{cloudfront_domain_name}/"
            ]
            
            logout_urls = [
                f"https://{cloudfront_domain_name}/"
            ]
            
            # Update the CloudFormation properties
            cfn_user_pool_client.add_property_override("CallbackURLs", callback_urls)
            cfn_user_pool_client.add_property_override("LogoutURLs", logout_urls)
            
            print(f"Updated Cognito callback URLs to include: https://{cloudfront_domain_name}")
            
        except Exception as e:
            print(f"Warning: Could not update Cognito callback URLs: {e}")
            # This is not critical for stack deployment, so we continue
    
    def create_user_pool(self):
        """Create the Cognito User Pool."""
        self.user_pool = cognito.UserPool(
            self, "UserPool",
            user_pool_name=self._user_pool_name,
            self_sign_up_enabled=True,
            sign_in_case_sensitive=False,
            auto_verify=cognito.AutoVerifiedAttrs(email=True),
            sign_in_aliases=cognito.SignInAliases(
                email=True,
                username=True
            ),
            standard_attributes=cognito.StandardAttributes(
                email=cognito.StandardAttribute(
                    required=True,
                    mutable=True
                ),
                given_name=cognito.StandardAttribute(
                    required=True,
                    mutable=True
                ),
                family_name=cognito.StandardAttribute(
                    required=True,
                    mutable=True
                )
            ),
            password_policy=cognito.PasswordPolicy(
                min_length=8,
                require_lowercase=True,
                require_uppercase=True,
                require_digits=True,
                require_symbols=True,
                temp_password_validity=Duration.days(7)
            ),
            account_recovery=cognito.AccountRecovery.EMAIL_ONLY,
            removal_policy=RemovalPolicy.DESTROY
        )
    
    def create_user_pool_domain(self):
        """Create the Cognito User Pool Domain."""
        self.user_pool_domain = cognito.UserPoolDomain(
            self, "UserPoolDomain",
            user_pool=self.user_pool,
            cognito_domain=cognito.CognitoDomainOptions(
                domain_prefix=f"{self._stack_name.lower()}-{COGNITO_DOMAIN_NAME}"
            )
        )
    
    def create_user_pool_client(self):
        """Create the User Pool Client."""
        self.user_pool_client = cognito.UserPoolClient(
            self, "UserPoolClient",
            user_pool=self.user_pool,
            user_pool_client_name=f"{self._stack_name}-user-pool-client",
            generate_secret=False,
            auth_flows=cognito.AuthFlow(
                user_password=True,
                user_srp=True,
                admin_user_password=True
            ),
            supported_identity_providers=[
                cognito.UserPoolClientIdentityProvider.COGNITO
            ],
            o_auth=cognito.OAuthSettings(
                flows=cognito.OAuthFlows(
                    authorization_code_grant=True
                ),
                scopes=[
                    cognito.OAuthScope.OPENID,
                    cognito.OAuthScope.EMAIL,
                    cognito.OAuthScope.PROFILE
                ],
                callback_urls=["http://localhost:3000"],  # Placeholder - will be updated post-deployment
                logout_urls=["http://localhost:3000"]  # Placeholder - will be updated post-deployment
            ),
            read_attributes=cognito.ClientAttributes().with_standard_attributes(
                email=True,
                given_name=True,
                family_name=True
            ),
            write_attributes=cognito.ClientAttributes().with_standard_attributes(
                email=True,
                given_name=True,
                family_name=True
            ),
            access_token_validity=Duration.hours(1),
            id_token_validity=Duration.hours(1),
            refresh_token_validity=Duration.days(30)
        )
    
    def create_auth_lambda(self):
        """Create the authentication Lambda function."""
        self.auth_lambda = _lambda.Function(
            self, "AuthLambda",
            function_name=f"{self._stack_name}-auth-lambda",
            runtime=_lambda.Runtime.PYTHON_3_9,
            handler="lambda_function.lambda_handler",
            code=_lambda.Code.from_asset("one_l/authorization"),
            timeout=Duration.seconds(30),
            memory_size=128,
            # Keep using log_retention (deprecated but stable) to avoid creating new LogGroup resources
            log_retention=logs.RetentionDays.ONE_WEEK,
            environment={
                "USER_POOL_ID": self.user_pool.user_pool_id,
                "USER_POOL_CLIENT_ID": self.user_pool_client.user_pool_client_id,
                "LOG_LEVEL": "INFO"
            }
        )
        
        # Grant Lambda permissions to access Cognito
        self.auth_lambda.add_to_role_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=[
                    "cognito-idp:AdminGetUser",
                    "cognito-idp:AdminInitiateAuth",
                    "cognito-idp:GetUser",
                    "cognito-idp:AdminUpdateUserAttributes",
                    "cognito-idp:ListUsers"
                ],
                resources=[self.user_pool.user_pool_arn]
            )
        )
    
    def create_outputs(self):
        """Create CloudFormation outputs."""
        CfnOutput(
            self, "UserPoolId",
            value=self.user_pool.user_pool_id,
            description="Cognito User Pool ID",
            export_name=f"{self._stack_name}-UserPoolId"
        )
        
        CfnOutput(
            self, "UserPoolClientId", 
            value=self.user_pool_client.user_pool_client_id,
            description="Cognito User Pool Client ID",
            export_name=f"{self._stack_name}-UserPoolClientId"
        )
        
        CfnOutput(
            self, "UserPoolDomainUrl",
            value=f"https://{self.user_pool_domain.domain_name}.auth.{Stack.of(self).region}.amazoncognito.com",
            description="Cognito User Pool Domain URL",
            export_name=f"{self._stack_name}-UserPoolDomainUrl"
        )

    
    def add_user_group(self, group_name: str, description: str = None):
        """Add a user group to the user pool."""
        return cognito.CfnUserPoolGroup(
            self, f"{group_name}Group",
            user_pool_id=self.user_pool.user_pool_id,
            group_name=f"{self._stack_name}-{group_name}",
            description=description or f"{group_name} user group for {self._stack_name}"
        ) 