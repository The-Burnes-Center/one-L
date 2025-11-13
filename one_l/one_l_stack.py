from aws_cdk import Stack
from constructs import Construct
from .authorization.authorization import AuthorizationConstruct
from .agent_api.agent_api import AgentApiConstruct
from .api_gateway.api_gateway import ApiGatewayConstruct
from .user_interface.user_interface import UserInterfaceConstruct
# Removed unused imports - using environment variables instead

class OneLStack(Stack):

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # Create authorization construct
        self.authorization = AuthorizationConstruct(
            self, "Authorization"
        )
        
        # Create agent API construct with authorization reference
        self.agent_api = AgentApiConstruct(
            self, "AgentApi",
            authorization=self.authorization
        )

        # Create API Gateway construct with reference to functions
        self.api_gateway = ApiGatewayConstruct(
            self, "ApiGateway",
            functions_construct=self.agent_api.functions
        )
        
        # Create user interface construct with references to authorization and API Gateway
        self.user_interface = UserInterfaceConstruct(
            self, "UserInterface",
            authorization_construct=self.authorization,
            api_gateway_construct=self.api_gateway,
            agent_api_construct=self.agent_api
        )
        
        # Update Cognito callback URLs with CloudFront domain name
        self.authorization.update_callback_urls(
            self.user_interface.cloudfront_distribution.distribution_domain_name
        )