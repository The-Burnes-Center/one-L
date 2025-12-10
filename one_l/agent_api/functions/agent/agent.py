"""
Agent functions construct for AI-powered document review operations.
"""

import os
from typing import Optional
from constructs import Construct
from aws_cdk import (
    aws_lambda as _lambda,
    aws_iam as iam,
    aws_s3 as s3,
    aws_dynamodb as dynamodb,
    aws_opensearchserverless as aoss,
    aws_logs as logs,
    Duration,
    Stack,
    RemovalPolicy
)


class AgentConstruct(Construct):
    """
    Agent construct that pieces together individual Lambda functions for AI-powered document review.
    Follows the same pattern as KnowledgeManagementConstruct.
    """
    
    def __init__(
        self, 
        scope: Construct, 
        construct_id: str,
        knowledge_bucket: s3.Bucket,
        user_documents_bucket: s3.Bucket,
        agent_processing_bucket: s3.Bucket,
        analysis_table: dynamodb.Table,
        opensearch_collection: aoss.CfnCollection,
        knowledge_base_id: str,
        iam_roles,
        **kwargs
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)
        
        # Store references
        self.knowledge_bucket = knowledge_bucket
        self.user_documents_bucket = user_documents_bucket
        self.agent_processing_bucket = agent_processing_bucket
        self.analysis_table = analysis_table
        self.opensearch_collection = opensearch_collection
        self.knowledge_base_id = knowledge_base_id
        self.buckets = [knowledge_bucket, user_documents_bucket, agent_processing_bucket]
        self.iam_roles = iam_roles
        
        # Configuration
        self._stack_name = Stack.of(self).stack_name
        
        # Instance variables for Lambda functions
        self.stepfunctions_construct = None
        
        # Create Step Functions construct
        self._create_stepfunctions_construct()
    
    def _create_stepfunctions_construct(self):
        """Create Step Functions construct."""
        from ..stepfunctions.stepfunctions import StepFunctionsConstruct
        
        self.stepfunctions_construct = StepFunctionsConstruct(
            self, "StepFunctions",
            knowledge_bucket=self.knowledge_bucket,
            user_documents_bucket=self.user_documents_bucket,
            agent_processing_bucket=self.agent_processing_bucket,
            analysis_table=self.analysis_table,
            opensearch_collection=self.opensearch_collection,
            knowledge_base_id=self.knowledge_base_id,
            iam_roles=self.iam_roles
        )
    
    def get_function_routes(self) -> dict:
        """
        Get function routing metadata for API Gateway.
        
        Returns a dictionary defining available functions and their routing configurations.
        Uses Step Functions workflow for document review.
        """
        
        if not self.stepfunctions_construct:
            raise ValueError("Step Functions construct must be initialized")
        
        return {
            "review": {
                "function": self.stepfunctions_construct.start_workflow_fn,
                "path": "review",
                "methods": ["POST"],
                "description": "AI-powered document review with Step Functions workflow"
            },
            "job-status": {
                "function": self.stepfunctions_construct.job_status_fn,
                "path": "job-status",
                "methods": ["GET", "POST"],
                "description": "Get real-time status of a document review job"
            }
        } 