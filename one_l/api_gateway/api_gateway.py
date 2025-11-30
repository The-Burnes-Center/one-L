"""
API Gateway construct that routes requests to Agent API functions.
"""

from typing import Optional, Dict, Any
from constructs import Construct
from aws_cdk import (
    aws_apigateway as apigateway,
    aws_lambda as _lambda,
    aws_stepfunctions as sfn,
    aws_iam as iam,
    Stack,
    CfnOutput,
    Duration
)
import os


class ApiGatewayConstruct(Construct):
    """
    Generic API Gateway construct that routes requests to existing Lambda functions.
    """
    
    def __init__(
        self, 
        scope: Construct, 
        construct_id: str,
        functions_construct=None,
        **kwargs
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)
        
        # Store reference to functions construct
        self.functions = functions_construct
        
        # Instance variables
        self.main_api = None
        
        # Configuration
        self._stack_name = Stack.of(self).stack_name
        
        # Create the API Gateway
        self.create_main_api()
        
        # Create routes for available functions
        self.create_function_routes()
        
        # Create outputs
        self.create_outputs()
    

    
    def create_main_api(self):
        """Create the main API Gateway."""
        
        # Create API Gateway
        self.main_api = apigateway.RestApi(
            self, "MainApi",
            rest_api_name=f"{self._stack_name}-main-api",
            description="Main API Gateway for Agent API routing",
            default_cors_preflight_options=apigateway.CorsOptions(
                allow_origins=apigateway.Cors.ALL_ORIGINS,
                allow_methods=apigateway.Cors.ALL_METHODS,
                allow_headers=["Content-Type", "X-Amz-Date", "Authorization", "X-Api-Key", "X-Amz-Security-Token"],
            ),
        )
        
        # Add gateway responses to ensure CORS headers are returned for all responses
        self.main_api.add_gateway_response(
            "DEFAULT_4XX",
            type=apigateway.ResponseType.DEFAULT_4_XX,
            response_headers={
                "Access-Control-Allow-Origin": "'*'",
                "Access-Control-Allow-Methods": "'GET,POST,PUT,DELETE,OPTIONS'",
                "Access-Control-Allow-Headers": "'Content-Type,X-Amz-Date,Authorization,X-Api-Key,X-Amz-Security-Token'"
            }
        )
        
        self.main_api.add_gateway_response(
            "DEFAULT_5XX",
            type=apigateway.ResponseType.DEFAULT_5_XX,
            response_headers={
                "Access-Control-Allow-Origin": "'*'",
                "Access-Control-Allow-Methods": "'GET,POST,PUT,DELETE,OPTIONS'",
                "Access-Control-Allow-Headers": "'Content-Type,X-Amz-Date,Authorization,X-Api-Key,X-Amz-Security-Token'"
            }
        )
    
    def create_function_routes(self):
        """Create routes for all available functions."""
        
        if not self.functions:
            return
        
        # Get function definitions from the functions construct
        function_definitions = self.get_function_definitions()
        
        # Create routes for each function category
        for category, functions in function_definitions.items():
            self.create_category_routes(category, functions)
    
    def get_function_definitions(self) -> Dict[str, Dict[str, Any]]:
        """Get function definitions from the functions construct."""
        
        # Get function routing metadata from the functions construct
        # This makes the API Gateway completely generic and not tied to specific function types
        return self.functions.get_function_routes() if self.functions else {}
    
    def create_category_routes(self, category: str, functions: Dict[str, Any]):
        """Create routes for a specific function category."""
        
        # Create category resource
        category_resource = self.main_api.root.add_resource(category)
        
        # Create routes for each function in the category
        for func_name, func_config in functions.items():
            # Check if this is a Step Functions route or Lambda route
            if "state_machine" in func_config:
                self.create_stepfunctions_route(
                    category_resource,
                    func_name,
                    func_config["state_machine"],
                    func_config["methods"],
                    func_config["path"]
                )
            else:
                self.create_function_route(
                    category_resource,
                    func_name,
                    func_config["function"],
                    func_config["methods"],
                    func_config["path"]
                )
    
    def create_function_route(
        self,
        parent_resource: apigateway.Resource,
        function_name: str,
        lambda_function: _lambda.Function,
        methods: list,
        path: str
    ):
        """Create a route for a specific function."""
        
        # Create function resource
        function_resource = parent_resource.add_resource(path)
        
        # Create Lambda proxy integration
        # The Lambda function handles all response formatting including CORS headers
        integration = apigateway.LambdaIntegration(
            lambda_function,
            proxy=True,  # Use Lambda proxy integration
            timeout=Duration.seconds(29),  # Maximum API Gateway timeout (29 seconds)
        )
        
        # Add methods to the resource
        for method in methods:
            function_resource.add_method(
                method,
                integration
            )
        
        # Note: OPTIONS method is automatically added by default_cors_preflight_options
    
    def create_stepfunctions_route(
        self,
        parent_resource: apigateway.Resource,
        function_name: str,
        state_machine: sfn.StateMachine,
        methods: list,
        path: str
    ):
        """Create a route for a Step Functions state machine."""
        
        # Create function resource
        function_resource = parent_resource.add_resource(path)
        
        # Create IAM role for API Gateway to invoke Step Functions
        stepfunctions_role = iam.Role(
            self, f"{function_name}StepFunctionsRole",
            assumed_by=iam.ServicePrincipal("apigateway.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AWSLambdaBasicExecutionRole"
                )
            ]
        )
        
        # Grant permission to start Step Functions execution
        state_machine.grant_start_execution(stepfunctions_role)
        
        # Create Step Functions integration
        # Note: API Gateway doesn't have native Step Functions integration,
        # so we'll need to use AWS service integration
        integration = apigateway.AwsIntegration(
            service="states",
            action="StartExecution",
            options=apigateway.IntegrationOptions(
                credentials_role=stepfunctions_role,
                integration_responses=[
                    apigateway.IntegrationResponse(
                        status_code="200",
                        response_parameters={
                            "method.response.header.Access-Control-Allow-Origin": "'*'"
                        }
                    )
                ],
                request_templates={
                    "application/json": f'{{"stateMachineArn": "{state_machine.state_machine_arn}", "input": "$util.escapeJavaScript($input.body)"}}'
                }
            )
        )
        
        # Add methods to the resource
        for method in methods:
            method_response = apigateway.MethodResponse(
                status_code="200",
                response_parameters={
                    "method.response.header.Access-Control-Allow-Origin": True
                }
            )
            
            function_resource.add_method(
                method,
                integration,
                method_responses=[method_response]
            )
    
    def create_outputs(self):
        """Create CloudFormation outputs."""
        if not self.main_api:
            return
            
        # Main API URL
        CfnOutput(
            self, "MainApiUrl",
            value=self.main_api.url,
            description="Main API Gateway URL",
            export_name=f"{self._stack_name}-MainApiUrl"
        )
