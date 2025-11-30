"""
Step Functions construct for document review workflow.
Creates all Lambda functions and Step Functions state machine.
"""

import os
from typing import Optional
from constructs import Construct
from aws_cdk import (
    aws_lambda as _lambda,
    aws_stepfunctions as sfn,
    aws_stepfunctions_tasks as tasks,
    aws_iam as iam,
    aws_s3 as s3,
    aws_dynamodb as dynamodb,
    aws_opensearchserverless as aoss,
    aws_logs as logs,
    Duration,
    Stack,
    RemovalPolicy
)


class StepFunctionsConstruct(Construct):
    """
    Step Functions construct for document review workflow.
    Creates all Lambda functions and state machine.
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
        self._stack_name = Stack.of(self).stack_name
        
        # Create all Lambda functions
        self.create_lambda_functions()
        
        # Create Step Functions state machine
        self.create_state_machine()
    
    def create_lambda_functions(self):
        """Create all Lambda functions for Step Functions workflow."""
        
        # Create role for Step Functions Lambda functions
        role = self.iam_roles.create_agent_role(
            "StepFunctions",
            self.buckets,
            self.analysis_table,
            self.opensearch_collection
        )
        
        # Common environment variables
        common_env = {
            "KNOWLEDGE_BUCKET": self.knowledge_bucket.bucket_name,
            "USER_DOCUMENTS_BUCKET": self.user_documents_bucket.bucket_name,
            "AGENT_PROCESSING_BUCKET": self.agent_processing_bucket.bucket_name,
            "ANALYSES_TABLE_NAME": self.analysis_table.table_name,
            "KNOWLEDGE_BASE_ID": self.knowledge_base_id,
            "REGION": Stack.of(self).region,
            "LOG_LEVEL": "INFO"
        }
        
        # Create all Lambda functions
        self.initialize_job_fn = self._create_lambda(
            "InitializeJob",
            "initialize_job/lambda_function.lambda_handler",
            role,
            common_env,
            timeout=Duration.seconds(30)
        )
        
        self.split_document_fn = self._create_lambda(
            "SplitDocument",
            "split_document/lambda_function.lambda_handler",
            role,
            common_env,
            timeout=Duration.minutes(5)
        )
        
        self.analyze_chunk_structure_fn = self._create_lambda(
            "AnalyzeChunkStructure",
            "analyze_chunk_structure/lambda_function.lambda_handler",
            role,
            common_env,
            timeout=Duration.minutes(15)
        )
        
        self.analyze_document_structure_fn = self._create_lambda(
            "AnalyzeDocumentStructure",
            "analyze_document_structure/lambda_function.lambda_handler",
            role,
            common_env,
            timeout=Duration.minutes(15)
        )
        
        self.retrieve_kb_query_fn = self._create_lambda(
            "RetrieveKBQuery",
            "retrieve_kb_query/lambda_function.lambda_handler",
            role,
            common_env,
            timeout=Duration.minutes(2)
        )
        
        self.analyze_chunk_with_kb_fn = self._create_lambda(
            "AnalyzeChunkWithKB",
            "analyze_chunk_with_kb/lambda_function.lambda_handler",
            role,
            common_env,
            timeout=Duration.minutes(15)
        )
        
        self.analyze_document_with_kb_fn = self._create_lambda(
            "AnalyzeDocumentWithKB",
            "analyze_document_with_kb/lambda_function.lambda_handler",
            role,
            common_env,
            timeout=Duration.minutes(15)
        )
        
        self.merge_chunk_results_fn = self._create_lambda(
            "MergeChunkResults",
            "merge_chunk_results/lambda_function.lambda_handler",
            role,
            common_env,
            timeout=Duration.minutes(2)
        )
        
        self.generate_redline_fn = self._create_lambda(
            "GenerateRedline",
            "generate_redline/lambda_function.lambda_handler",
            role,
            common_env,
            timeout=Duration.minutes(10)
        )
        
        self.save_results_fn = self._create_lambda(
            "SaveResults",
            "save_results/lambda_function.lambda_handler",
            role,
            common_env,
            timeout=Duration.seconds(30)
        )
        
        self.cleanup_session_fn = self._create_lambda(
            "CleanupSession",
            "cleanup_session/lambda_function.lambda_handler",
            role,
            common_env,
            timeout=Duration.seconds(30)
        )
        
        self.handle_error_fn = self._create_lambda(
            "HandleError",
            "handle_error/lambda_function.lambda_handler",
            role,
            common_env,
            timeout=Duration.seconds(30)
        )
    
    def _create_lambda(
        self,
        function_name: str,
        handler: str,
        role: iam.Role,
        environment: dict,
        timeout: Duration,
        memory_size: int = 2048
    ) -> _lambda.Function:
        """Helper to create Lambda function with automatic bundling."""
        # CDK will automatically build during deployment
        # If build/lambda-deployment.zip exists, use it (for CI/CD - faster)
        # Otherwise, CDK will bundle automatically using Docker (requires Docker running)
        if os.path.exists("build/lambda-deployment.zip"):
            # Use pre-built package - all Lambda functions share the same dependencies
            lambda_code = _lambda.Code.from_asset("build/lambda-deployment.zip")
        else:
            # Extract handler path (e.g., "initialize_job/lambda_function.lambda_handler")
            handler_parts = handler.split("/")
            handler_dir = handler_parts[0] if len(handler_parts) > 1 else "stepfunctions"
            
            # CDK automatic bundling - builds on-the-fly during cdk deploy
            # Note: Requires Docker to be running
            lambda_code = _lambda.Code.from_asset(
                ".",
                bundling=_lambda.BundlingOptions(
                    image=_lambda.DockerImage.from_registry("public.ecr.aws/lambda/python:3.12"),
                    command=[
                        "bash", "-c",
                        f"""
                        # Install system dependencies for native packages (lxml, etc.)
                        dnf update -y && dnf install -y gcc gcc-c++ libxml2-devel libxslt-devel python3-devel zip && \
                        # Install Python dependencies
                        pip install --upgrade pip setuptools wheel && \
                        pip install --no-cache-dir -r one_l/agent_api/functions/agent/document_review/requirements.txt -t /asset-output && \
                        # Copy the specific Lambda function
                        cp one_l/agent_api/functions/stepfunctions/{handler_dir}/lambda_function.py /asset-output/ && \
                        # Copy all agent modules (shared across all Lambda functions)
                        mkdir -p /asset-output/agent_api/agent && \
                        cp -r one_l/agent_api/agent/* /asset-output/agent_api/agent/ && \
                        # Copy constants
                        cp constants.py /asset-output/ 2>/dev/null || true && \
                        # Clean up cache files
                        find /asset-output -type d -name __pycache__ -exec rm -rf {{}} + 2>/dev/null || true
                        """
                    ],
                    user="root"
                )
            )
        
        # Create log group with retention
        log_group = logs.LogGroup(
            self, f"{function_name}LogGroup",
            log_group_name=f"/aws/lambda/{self._stack_name}-stepfunctions-{function_name.lower()}",
            retention=logs.RetentionDays.ONE_WEEK,
            removal_policy=RemovalPolicy.DESTROY
        )
        
        return _lambda.Function(
            self, f"{function_name}Function",
            function_name=f"{self._stack_name}-stepfunctions-{function_name.lower()}",
            runtime=_lambda.Runtime.PYTHON_3_12,
            handler=handler,
            code=lambda_code,
            role=role,
            timeout=timeout,
            memory_size=memory_size,
            environment=environment,
            log_group=log_group
        )
    
    def create_state_machine(self):
        """Create Step Functions state machine with complete workflow."""
        
        # Error handler (define early so it can be used in catch blocks)
        handle_error = tasks.LambdaInvoke(
            self, "HandleError",
            lambda_function=self.handle_error_fn,
            output_path="$.Payload"
        )
        
        # Initialize job
        initialize_job = tasks.LambdaInvoke(
            self, "InitializeJob",
            lambda_function=self.initialize_job_fn,
            output_path="$.Payload",
            retry_on_service_exceptions=True
        )
        initialize_job.add_retry(
            errors=[sfn.Errors.TIMEOUT, sfn.Errors.TASKS_FAILED],
            interval=Duration.seconds(2),
            max_attempts=3,
            backoff_rate=2.0
        )
        initialize_job.add_catch(
            handle_error,
            errors=["States.ALL"],
            result_path="$.error"
        )
        
        # Split document
        split_document = tasks.LambdaInvoke(
            self, "SplitDocument",
            lambda_function=self.split_document_fn,
            output_path="$.Payload",
            retry_on_service_exceptions=True
        )
        split_document.add_retry(
            errors=[sfn.Errors.TIMEOUT, sfn.Errors.TASKS_FAILED],
            interval=Duration.seconds(2),
            max_attempts=2,
            backoff_rate=2.0
        )
        split_document.add_catch(
            handle_error,
            errors=["States.ALL"],
            result_path="$.error"
        )
        
        # Check chunk count
        check_chunk_count = sfn.Choice(self, "CheckChunkCount")
        
        # ===== CHUNKED PATH (chunks > 1) =====
        
        # Analyze chunk structure (gets queries for each chunk)
        analyze_chunk_structure = tasks.LambdaInvoke(
            self, "AnalyzeChunkStructure",
            lambda_function=self.analyze_chunk_structure_fn,
            output_path="$.Payload",
            retry_on_service_exceptions=True
        )
        analyze_chunk_structure.add_retry(
            errors=[sfn.Errors.TIMEOUT, sfn.Errors.TASKS_FAILED],
            interval=Duration.seconds(2),
            max_attempts=2,
            backoff_rate=2.0
        )
        
        # Retrieve KB queries in parallel
        retrieve_kb_queries_map = sfn.Map(
            self, "RetrieveKBQueriesParallel",
            items_path="$.queries",
            max_concurrency=20,
            result_path="$.kb_results"
        )
        
        retrieve_kb_query = tasks.LambdaInvoke(
            self, "RetrieveKBQuery",
            lambda_function=self.retrieve_kb_query_fn,
            output_path="$.Payload",
            retry_on_service_exceptions=True
        )
        retrieve_kb_query.add_retry(
            errors=[sfn.Errors.TIMEOUT, sfn.Errors.TASKS_FAILED],
            interval=Duration.seconds(2),
            max_attempts=3,
            backoff_rate=2.0
        )
        
        retrieve_kb_queries_map.item_processor(retrieve_kb_query)
        
        # Analyze chunk with KB results
        analyze_chunk_with_kb = tasks.LambdaInvoke(
            self, "AnalyzeChunkWithKB",
            lambda_function=self.analyze_chunk_with_kb_fn,
            output_path="$.Payload",
            retry_on_service_exceptions=True
        )
        analyze_chunk_with_kb.add_retry(
            errors=[sfn.Errors.TIMEOUT, sfn.Errors.TASKS_FAILED],
            interval=Duration.seconds(2),
            max_attempts=2,
            backoff_rate=2.0
        )
        
        # Chunk workflow: structure -> queries -> analysis
        chunk_workflow = analyze_chunk_structure.next(
            retrieve_kb_queries_map.next(analyze_chunk_with_kb)
        )
        
        # Process all chunks in parallel
        analyze_chunks_map = sfn.Map(
            self, "AnalyzeChunksParallel",
            items_path="$.chunks",
            max_concurrency=10,
            result_path="$.chunk_analyses"
        )
        
        analyze_chunks_map.item_processor(chunk_workflow)
        
        # Merge chunk results
        merge_chunk_results = tasks.LambdaInvoke(
            self, "MergeChunkResults",
            lambda_function=self.merge_chunk_results_fn,
            output_path="$.Payload",
            retry_on_service_exceptions=True
        )
        merge_chunk_results.add_retry(
            errors=[sfn.Errors.TIMEOUT, sfn.Errors.TASKS_FAILED],
            interval=Duration.seconds(2),
            max_attempts=2,
            backoff_rate=2.0
        )
        
        # ===== SINGLE DOCUMENT PATH (chunks = 1) =====
        
        analyze_doc_structure = tasks.LambdaInvoke(
            self, "AnalyzeDocumentStructure",
            lambda_function=self.analyze_document_structure_fn,
            output_path="$.Payload",
            retry_on_service_exceptions=True
        )
        analyze_doc_structure.add_retry(
            errors=[sfn.Errors.TIMEOUT, sfn.Errors.TASKS_FAILED],
            interval=Duration.seconds(2),
            max_attempts=2,
            backoff_rate=2.0
        )
        
        # Retrieve KB queries in parallel
        retrieve_doc_kb_queries_map = sfn.Map(
            self, "RetrieveDocKBQueriesParallel",
            items_path="$.queries",
            max_concurrency=20,
            result_path="$.kb_results"
        )
        
        retrieve_doc_kb_query = tasks.LambdaInvoke(
            self, "RetrieveDocKBQuery",
            lambda_function=self.retrieve_kb_query_fn,
            output_path="$.Payload",
            retry_on_service_exceptions=True
        )
        retrieve_doc_kb_query.add_retry(
            errors=[sfn.Errors.TIMEOUT, sfn.Errors.TASKS_FAILED],
            interval=Duration.seconds(2),
            max_attempts=3,
            backoff_rate=2.0
        )
        
        retrieve_doc_kb_queries_map.item_processor(retrieve_doc_kb_query)
        
        # Analyze document with KB results
        analyze_document_with_kb = tasks.LambdaInvoke(
            self, "AnalyzeDocumentWithKB",
            lambda_function=self.analyze_document_with_kb_fn,
            output_path="$.Payload",
            retry_on_service_exceptions=True
        )
        analyze_document_with_kb.add_retry(
            errors=[sfn.Errors.TIMEOUT, sfn.Errors.TASKS_FAILED],
            interval=Duration.seconds(2),
            max_attempts=2,
            backoff_rate=2.0
        )
        
        # Single document workflow
        single_doc_workflow = analyze_doc_structure.next(
            retrieve_doc_kb_queries_map.next(analyze_document_with_kb)
        )
        
        # ===== COMMON FINAL STEPS =====
        
        # Generate redline
        generate_redline = tasks.LambdaInvoke(
            self, "GenerateRedline",
            lambda_function=self.generate_redline_fn,
            output_path="$.Payload",
            retry_on_service_exceptions=True
        )
        generate_redline.add_retry(
            errors=[sfn.Errors.TIMEOUT, sfn.Errors.TASKS_FAILED],
            interval=Duration.seconds(2),
            max_attempts=2,
            backoff_rate=2.0
        )
        
        # Save results
        save_results = tasks.LambdaInvoke(
            self, "SaveResults",
            lambda_function=self.save_results_fn,
            output_path="$.Payload",
            retry_on_service_exceptions=True
        )
        save_results.add_retry(
            errors=[sfn.Errors.TIMEOUT, sfn.Errors.TASKS_FAILED],
            interval=Duration.seconds(2),
            max_attempts=2,
            backoff_rate=2.0
        )
        
        # Cleanup session
        cleanup_session = tasks.LambdaInvoke(
            self, "CleanupSession",
            lambda_function=self.cleanup_session_fn,
            output_path="$.Payload",
            retry_on_service_exceptions=True
        )
        cleanup_session.add_retry(
            errors=[sfn.Errors.TIMEOUT, sfn.Errors.TASKS_FAILED],
            interval=Duration.seconds(2),
            max_attempts=2,
            backoff_rate=2.0
        )
        
        # Define workflow with proper branching
        chunked_path = analyze_chunks_map.next(merge_chunk_results)
        single_path = single_doc_workflow
        
        # Add error handling to individual states (not chains)
        generate_redline.add_catch(
            handle_error,
            errors=["States.ALL"],
            result_path="$.error"
        )
        save_results.add_catch(
            handle_error,
            errors=["States.ALL"],
            result_path="$.error"
        )
        cleanup_session.add_catch(
            handle_error,
            errors=["States.ALL"],
            result_path="$.error"
        )
        
        # Both paths converge to final steps
        final_steps = generate_redline.next(save_results).next(cleanup_session)
        
        # Complete workflow definition
        definition = initialize_job.next(
            split_document.next(
                check_chunk_count
                    .when(
                        sfn.Condition.number_greater_than("$.chunk_count", 1),
                        chunked_path.next(final_steps)
                    )
                    .otherwise(
                        single_path.next(final_steps)
                    )
            )
        )
        
        # Create state machine log group
        state_machine_log_group = logs.LogGroup(
            self, "StateMachineLogGroup",
            log_group_name=f"/aws/vendedlogs/states/{self._stack_name}-document-review",
            retention=logs.RetentionDays.ONE_WEEK,
            removal_policy=RemovalPolicy.DESTROY
        )
        
        # Create state machine
        self.state_machine = sfn.StateMachine(
            self, "DocumentReviewStateMachine",
            state_machine_name=f"{self._stack_name}-document-review",
            definition_body=sfn.DefinitionBody.from_chainable(definition),
            timeout=Duration.hours(2),
            logs=sfn.LogOptions(
                destination=state_machine_log_group,
                level=sfn.LogLevel.ALL
            )
        )

