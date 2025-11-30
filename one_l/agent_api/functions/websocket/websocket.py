"""
WebSocket functions construct for real-time document processing updates.
"""

from typing import Optional
from constructs import Construct
from aws_cdk import (
    aws_lambda as _lambda,
    aws_apigatewayv2 as apigatewayv2,
    aws_apigatewayv2_integrations as integrations,
    aws_iam as iam,
    aws_dynamodb as dynamodb,
    aws_logs as logs,
    Duration,
    Stack,
    RemovalPolicy,
    CfnOutput
)


class WebSocketConstruct(Construct):
    """
    WebSocket construct that creates Lambda functions for real-time communication.
    Follows the same pattern as KnowledgeManagementConstruct and AgentConstruct.
    """
    
    def __init__(
        self, 
        scope: Construct, 
        construct_id: str,
        iam_roles,
        **kwargs
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)
        
        # Store references
        self.iam_roles = iam_roles
        
        # Configuration
        self._stack_name = Stack.of(self).stack_name
        
        # Instance variables for Lambda functions and WebSocket API
        self.websocket_api = None
        self.connections_table = None
        self.connect_function = None
        self.disconnect_function = None
        self.message_function = None
        self.notification_function = None
        
        # Create WebSocket infrastructure
        self.create_connections_table()
        self.create_functions()
        self.create_websocket_api()
        self.create_outputs()
    
    def create_connections_table(self):
        """Create DynamoDB table for storing WebSocket connections."""
        
        self.connections_table = dynamodb.Table(
            self, "ConnectionsTable",
            table_name=f"{self._stack_name}-websocket-connections",
            partition_key=dynamodb.Attribute(
                name="connection_id",
                type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name="user_id",
                type=dynamodb.AttributeType.STRING
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.DESTROY,
            time_to_live_attribute="ttl"  # Auto-cleanup old connections
        )
        
        # Add GSI for querying by user_id
        self.connections_table.add_global_secondary_index(
            index_name="user-index",
            partition_key=dynamodb.Attribute(
                name="user_id",
                type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name="connected_at",
                type=dynamodb.AttributeType.STRING
            )
        )
    
    def create_functions(self):
        """Create WebSocket Lambda functions."""
        self.create_connect_function()
        self.create_disconnect_function()
        self.create_message_function()
        self.create_notification_function()
    
    def create_connect_function(self):
        """Create Lambda function for WebSocket connection handling."""
        
        # Create role with DynamoDB permissions
        role = self.iam_roles.create_websocket_role(
            "WebSocketConnect", 
            self.connections_table
        )
        
        # Create Lambda function
        self.connect_function = _lambda.Function(
            self, "WebSocketConnectFunction",
            function_name=f"{self._stack_name}-websocket-connect",
            runtime=_lambda.Runtime.PYTHON_3_9,
            handler="lambda_function.lambda_handler",
            code=_lambda.Code.from_asset("one_l/agent_api/functions/websocket/connect"),
            role=role,
            timeout=Duration.seconds(30),
            memory_size=128,
            log_retention=logs.RetentionDays.ONE_WEEK,
            environment={
                "CONNECTIONS_TABLE": self.connections_table.table_name,
                "LOG_LEVEL": "INFO"
            }
        )
    
    def create_disconnect_function(self):
        """Create Lambda function for WebSocket disconnection handling."""
        
        # Create role with DynamoDB permissions
        role = self.iam_roles.create_websocket_role(
            "WebSocketDisconnect", 
            self.connections_table
        )
        
        # Create Lambda function
        self.disconnect_function = _lambda.Function(
            self, "WebSocketDisconnectFunction",
            function_name=f"{self._stack_name}-websocket-disconnect",
            runtime=_lambda.Runtime.PYTHON_3_9,
            handler="lambda_function.lambda_handler",
            code=_lambda.Code.from_asset("one_l/agent_api/functions/websocket/disconnect"),
            role=role,
            timeout=Duration.seconds(30),
            memory_size=128,
            log_retention=logs.RetentionDays.ONE_WEEK,
            environment={
                "CONNECTIONS_TABLE": self.connections_table.table_name,
                "LOG_LEVEL": "INFO"
            }
        )
    
    def create_message_function(self):
        """Create Lambda function for handling WebSocket messages."""
        
        # Create role with DynamoDB and API Gateway management permissions
        role = self.iam_roles.create_websocket_role(
            "WebSocketMessage", 
            self.connections_table
        )
        
        # Create Lambda function
        self.message_function = _lambda.Function(
            self, "WebSocketMessageFunction",
            function_name=f"{self._stack_name}-websocket-message",
            runtime=_lambda.Runtime.PYTHON_3_9,
            handler="lambda_function.lambda_handler",
            code=_lambda.Code.from_asset("one_l/agent_api/functions/websocket/message"),
            role=role,
            timeout=Duration.seconds(30),
            memory_size=128,
            log_retention=logs.RetentionDays.ONE_WEEK,
            environment={
                "CONNECTIONS_TABLE": self.connections_table.table_name,
                "LOG_LEVEL": "INFO"
            }
        )
    
    def create_notification_function(self):
        """Create Lambda function for sending notifications to WebSocket clients."""
        
        # Create role with DynamoDB and API Gateway management permissions
        role = self.iam_roles.create_websocket_role(
            "WebSocketNotification", 
            self.connections_table
        )
        
        # Create Lambda function
        self.notification_function = _lambda.Function(
            self, "WebSocketNotificationFunction",
            function_name=f"{self._stack_name}-websocket-notification",
            runtime=_lambda.Runtime.PYTHON_3_9,
            handler="lambda_function.lambda_handler",
            code=_lambda.Code.from_asset("one_l/agent_api/functions/websocket/notification"),
            role=role,
            timeout=Duration.seconds(60),
            memory_size=256,
            log_retention=logs.RetentionDays.ONE_WEEK,
            environment={
                "CONNECTIONS_TABLE": self.connections_table.table_name,
                "LOG_LEVEL": "INFO"
            }
        )
    
    def create_websocket_api(self):
        """Create WebSocket API Gateway."""
        
        # Create WebSocket API
        self.websocket_api = apigatewayv2.WebSocketApi(
            self, "WebSocketApi",
            api_name=f"{self._stack_name}-websocket-api",
            description="WebSocket API for real-time document processing updates",
            connect_route_options=apigatewayv2.WebSocketRouteOptions(
                integration=integrations.WebSocketLambdaIntegration(
                    "ConnectIntegration",
                    self.connect_function
                )
            ),
            disconnect_route_options=apigatewayv2.WebSocketRouteOptions(
                integration=integrations.WebSocketLambdaIntegration(
                    "DisconnectIntegration", 
                    self.disconnect_function
                )
            ),
            default_route_options=apigatewayv2.WebSocketRouteOptions(
                integration=integrations.WebSocketLambdaIntegration(
                    "DefaultIntegration",
                    self.message_function
                )
            )
        )
        
        # Create WebSocket stage
        self.websocket_stage = apigatewayv2.WebSocketStage(
            self, "WebSocketStage",
            web_socket_api=self.websocket_api,
            stage_name="prod",
            auto_deploy=True
        )
        
        # Update notification function with WebSocket API URL
        self.notification_function.add_environment(
            "WEBSOCKET_API_ENDPOINT", 
            f"https://{self.websocket_api.api_id}.execute-api.{Stack.of(self).region}.amazonaws.com/prod"
        )
        
        # Grant WebSocket API permissions to notification function
        self.notification_function.add_to_role_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=[
                    "execute-api:ManageConnections"
                ],
                resources=[
                    f"arn:aws:execute-api:{Stack.of(self).region}:{Stack.of(self).account}:{self.websocket_api.api_id}/*/*"
                ]
            )
        )
    
    def create_outputs(self):
        """Create CloudFormation outputs."""
        
        # WebSocket API URL
        CfnOutput(
            self, "WebSocketApiUrl",
            value=f"wss://{self.websocket_api.api_id}.execute-api.{Stack.of(self).region}.amazonaws.com/prod",
            description="WebSocket API URL",
            export_name=f"{self._stack_name}-WebSocketApiUrl"
        )

    
    def get_function_routes(self) -> dict:
        """
        Get function routing metadata for REST API Gateway (if needed).
        WebSocket functions primarily use WebSocket API, but can expose REST endpoints too.
        """
        
        return {
            "notify": {
                "function": self.notification_function,
                "path": "notify",
                "methods": ["POST"],
                "description": "Send notifications via WebSocket (REST fallback)"
            }
        }
    
    def get_websocket_api_url(self) -> str:
        """Get the WebSocket API URL."""
        return f"wss://{self.websocket_api.api_id}.execute-api.{Stack.of(self).region}.amazonaws.com/prod"
    
    def get_notification_function(self) -> _lambda.Function:
        """Get the notification function for integration with other services."""
        return self.notification_function