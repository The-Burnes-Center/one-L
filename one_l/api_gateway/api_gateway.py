"""
API Gateway construct that manages multiple API Gateway + Lambda pairs.
"""

from typing import Optional, Dict, Any, List
from constructs import Construct
from aws_cdk import (
    aws_apigateway as apigateway,
    aws_lambda as _lambda,
    aws_iam as iam,
    aws_logs as logs,
    Duration,
    RemovalPolicy,
    Stack,
    CfnOutput
)


class ApiGatewayConstruct(Construct):
    """
    API Gateway construct that creates multiple API Gateway + Lambda pairs.
    """
    
    def __init__(
        self, 
        scope: Construct, 
        construct_id: str,
        **kwargs
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)
        
        # Instance variables
        self.upload_api = None
        self.upload_lambda = None
        self.process_api = None
        self.process_lambda = None
        
        # Configuration - ensure all names start with stack name
        self._stack_name = Stack.of(self).stack_name
        
        # Create APIs
        self.create_upload_to_s3_api()
        # Add more APIs here as needed
        # self.create_process_data_api()
        
        # Create outputs
        self.create_outputs()
    
    def create_upload_to_s3_api(self):
        """Create the S3 upload API Gateway + Lambda."""
        
        # Create IAM role for Lambda
        lambda_role = iam.Role(
            self, "UploadLambdaRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AWSLambdaBasicExecutionRole"
                )
            ],
            inline_policies={
                "S3Access": iam.PolicyDocument(
                    statements=[
                        iam.PolicyStatement(
                            actions=[
                                "s3:PutObject",
                                "s3:PutObjectAcl",
                                "s3:GetObject",
                                "s3:HeadBucket",
                                "s3:ListBucket"
                            ],
                            resources=["*"],
                            effect=iam.Effect.ALLOW
                        )
                    ]
                )
            }
        )
        
        # Create Lambda function
        self.upload_lambda = _lambda.Function(
            self, "UploadToS3Lambda",
            function_name=f"{self._stack_name}-upload-to-s3",
            runtime=_lambda.Runtime.PYTHON_3_9,
            handler="lambda_function.lambda_handler",
            code=_lambda.Code.from_asset("one_l/api_gateway"),
            role=lambda_role,
            timeout=Duration.seconds(30),
            memory_size=512,
            environment={
                "LOG_LEVEL": "INFO"
            },
            log_retention=logs.RetentionDays.ONE_WEEK
        )
        
        # Create API Gateway
        self.upload_api = apigateway.RestApi(
            self, "UploadToS3Api",
            rest_api_name=f"{self._stack_name}-uploadToS3",
            description="API Gateway for S3 file uploads",
            default_cors_preflight_options=apigateway.CorsOptions(
                allow_origins=apigateway.Cors.ALL_ORIGINS,
                allow_methods=apigateway.Cors.ALL_METHODS,
                allow_headers=["Content-Type", "X-Amz-Date", "Authorization", "X-Api-Key"],
            ),
        )
        
        # Create Lambda integration
        lambda_integration = apigateway.LambdaIntegration(
            self.upload_lambda,
            integration_responses=[
                apigateway.IntegrationResponse(
                    status_code="200",
                    response_parameters={
                        "method.response.header.Access-Control-Allow-Origin": "'*'"
                    },
                )
            ],
        )
        
        # Create endpoints
        upload_resource = self.upload_api.root.add_resource("upload")
        upload_resource.add_method(
            "POST",
            lambda_integration,
            method_responses=[
                apigateway.MethodResponse(
                    status_code="200",
                    response_parameters={
                        "method.response.header.Access-Control-Allow-Origin": True
                    },
                )
            ],
        )
    
    def create_process_data_api(self):
        """Create the data processing API Gateway + Lambda."""
        
        # Create IAM role for Lambda
        lambda_role = iam.Role(
            self, "ProcessLambdaRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AWSLambdaBasicExecutionRole"
                )
            ],
            inline_policies={
                "ProcessAccess": iam.PolicyDocument(
                    statements=[
                        iam.PolicyStatement(
                            actions=[
                                "s3:GetObject",
                                "s3:PutObject",
                                "lambda:InvokeFunction"
                            ],
                            resources=["*"],
                            effect=iam.Effect.ALLOW
                        )
                    ]
                )
            }
        )
        
        # Create Lambda function
        self.process_lambda = _lambda.Function(
            self, "ProcessDataLambda",
            function_name=f"{self._stack_name}-process-data",
            runtime=_lambda.Runtime.PYTHON_3_9,
            handler="process_lambda_function.lambda_handler",
            code=_lambda.Code.from_asset("one_l/api_gateway"),
            role=lambda_role,
            timeout=Duration.seconds(60),
            memory_size=1024,
            environment={
                "LOG_LEVEL": "INFO"
            },
            log_retention=logs.RetentionDays.ONE_WEEK
        )
        
        # Create API Gateway
        self.process_api = apigateway.RestApi(
            self, "ProcessDataApi",
            rest_api_name=f"{self._stack_name}-processData",
            description="API Gateway for data processing",
            default_cors_preflight_options=apigateway.CorsOptions(
                allow_origins=apigateway.Cors.ALL_ORIGINS,
                allow_methods=apigateway.Cors.ALL_METHODS,
                allow_headers=["Content-Type", "X-Amz-Date", "Authorization", "X-Api-Key"],
            ),
        )
        
        # Create Lambda integration
        lambda_integration = apigateway.LambdaIntegration(
            self.process_lambda,
            integration_responses=[
                apigateway.IntegrationResponse(
                    status_code="200",
                    response_parameters={
                        "method.response.header.Access-Control-Allow-Origin": "'*'"
                    },
                )
            ],
        )
        
        # Create endpoints
        process_resource = self.process_api.root.add_resource("process")
        process_resource.add_method(
            "POST",
            lambda_integration,
            method_responses=[
                apigateway.MethodResponse(
                    status_code="200",
                    response_parameters={
                        "method.response.header.Access-Control-Allow-Origin": True
                    },
                )
            ],
        )
    
    def create_outputs(self):
        """Create CloudFormation outputs."""
        if self.upload_api:
            CfnOutput(
                self, "UploadApiUrl",
                value=self.upload_api.url,
                description="Upload API Gateway URL",
                export_name=f"{self._stack_name}-UploadApiUrl"
            )
            
            CfnOutput(
                self, "UploadEndpointUrl",
                value=f"{self.upload_api.url}upload",
                description="Upload endpoint URL",
                export_name=f"{self._stack_name}-UploadEndpointUrl"
            )
        
        if self.process_api:
            CfnOutput(
                self, "ProcessApiUrl",
                value=self.process_api.url,
                description="Process API Gateway URL",
                export_name=f"{self._stack_name}-ProcessApiUrl"
            )
            
            CfnOutput(
                self, "ProcessEndpointUrl",
                value=f"{self.process_api.url}process",
                description="Process endpoint URL",
                export_name=f"{self._stack_name}-ProcessEndpointUrl"
            )
    
    def add_new_api(self, api_name: str, lambda_handler: str, endpoints: List[str], permissions: List[str]):
        """
        Helper method to add new APIs easily.
        
        Args:
            api_name: Name of the API
            lambda_handler: Lambda handler function name
            endpoints: List of endpoint paths
            permissions: List of IAM permissions needed
        """
        # This is a template method that can be customized for each new API
        # Implementation would be similar to create_upload_to_s3_api()
        pass