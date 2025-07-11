from aws_cdk import Stack
from constructs import Construct
from .authorization.authorization import AuthorizationConstruct
from .agent_api.agent_api import AgentApiConstruct

class OneLStack(Stack):

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # # Create authorization construct
        # self.authorization = AuthorizationConstruct(
        #     self, "Authorization"
        # )
        
        # # Create agent API construct
        # self.agent_api = AgentApiConstruct(
        #     self, "AgentApi"
        # )