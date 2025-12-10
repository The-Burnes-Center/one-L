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
        
        # Store Lambda function references for later updates
        self.lambda_functions = []
        
        # Create all Lambda functions
        self.create_lambda_functions()
        
        # Create Step Functions state machine
        self.create_state_machine()
        
        # Create the wrapper Lambda that starts the workflow
        # This is used by API Gateway to return job_id immediately
        self.create_start_workflow_lambda()
    
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
            timeout=Duration.minutes(2)
        )
        
        self.split_document_fn = self._create_lambda(
            "SplitDocument",
            "split_document/lambda_function.lambda_handler",
            role,
            common_env,
            timeout=Duration.minutes(15)
        )
        
        # Unified lambda functions (replace duplicate chunk/document functions)
        self.analyze_structure_fn = self._create_lambda(
            "AnalyzeStructure",
            "analyze_structure/lambda_function.lambda_handler",
            role,
            common_env,
            timeout=Duration.minutes(15)
        )
        
        self.retrieve_all_kb_queries_fn = self._create_lambda(
            "RetrieveAllKBQueries",
            "retrieve_all_kb_queries/lambda_function.lambda_handler",
            role,
            common_env,
            timeout=Duration.minutes(15)  # Longer timeout for retrieving all queries
        )
        
        self.identify_conflicts_fn = self._create_lambda(
            "IdentifyConflicts",
            "identify_conflicts/lambda_function.lambda_handler",
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
            timeout=Duration.minutes(15)
        )
        
        self.save_results_fn = self._create_lambda(
            "SaveResults",
            "save_results/lambda_function.lambda_handler",
            role,
            common_env,
            timeout=Duration.minutes(2)
        )
        
        self.cleanup_session_fn = self._create_lambda(
            "CleanupSession",
            "cleanup_session/lambda_function.lambda_handler",
            role,
            common_env,
            timeout=Duration.minutes(2)
        )
        
        self.handle_error_fn = self._create_lambda(
            "HandleError",
            "handle_error/lambda_function.lambda_handler",
            role,
            common_env,
            timeout=Duration.minutes(2)
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
                        pip install --no-cache-dir -r one_l/agent_api/functions/stepfunctions/requirements.txt -t /asset-output && \
                        # Fix SyntaxWarning in python-docx library (invalid escape sequence \d)
                        python3 -c "import os,re; [open(f,'w').write(open(f,'r').read().replace('headerPattern = re.compile(\".*Heading (\\\\d+)$\")','headerPattern = re.compile(r\".*Heading (\\\\d+)$\")')) for root,dirs,files in os.walk('/asset-output') for f in [os.path.join(root,file) for file in files if file=='paragraph.py' and 'docx/text' in root]]" 2>/dev/null || true && \
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
        
        func = _lambda.Function(
            self, f"{function_name}Function",
            function_name=f"{self._stack_name}-stepfunctions-{function_name.lower()}",
            runtime=_lambda.Runtime.PYTHON_3_12,
            handler=handler,
            code=lambda_code,
            role=role,
            timeout=timeout,
            memory_size=memory_size,
            environment=environment,
            # Keep using log_retention (deprecated but stable) to avoid creating new LogGroup resources
            log_retention=logs.RetentionDays.ONE_WEEK
        )
        
        # Store reference for later updates
        self.lambda_functions.append(func)
        
        return func
    
    def create_state_machine(self):
        """Create Step Functions state machine with complete workflow."""
        
        # Error handler (define early so it can be used in catch blocks)
        # Use result_path to merge error output with existing state (preserves session_id, user_id)
        handle_error = tasks.LambdaInvoke(
            self, "HandleError",
            lambda_function=self.handle_error_fn,
            result_path="$.error_result"  # Merge instead of replace to preserve context
        )
        
        # Cleanup session - ensure it runs after error handling
        cleanup_session = tasks.LambdaInvoke(
            self, "CleanupSession",
            lambda_function=self.cleanup_session_fn,
            payload_response_only=True,
            result_path="$.cleanup_result",
            retry_on_service_exceptions=True,
            payload=sfn.TaskInput.from_object({
                "session_id": sfn.JsonPath.string_at("$.session_id"),
                "user_id": sfn.JsonPath.string_at("$.user_id")
            })
        )
        cleanup_session.add_retry(
            errors=[sfn.Errors.TIMEOUT, sfn.Errors.TASKS_FAILED],
            interval=Duration.seconds(2),
            max_attempts=2,
            backoff_rate=2.0
        )
        
        # Chain error handling with cleanup - ensures cleanup runs after any error
        handle_error_chain = handle_error.next(cleanup_session)
        
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
            handle_error_chain,
            errors=["States.ALL"],
            result_path="$.error"
        )
        
        # =====================================================
        # STEP FUNCTIONS DATA FLOW (AWS Best Practices):
        # - Use result_path to MERGE Lambda output with state
        # - Use payload to explicitly SELECT what Lambda receives
        # - Context (job_id, session_id, etc.) flows through entire workflow
        # - Each Lambda output goes to a specific path ($.split_result, etc.)
        # =====================================================
        
        # Split document - merge result, preserve context
        split_document = tasks.LambdaInvoke(
            self, "SplitDocument",
            lambda_function=self.split_document_fn,
            payload_response_only=True,
            result_path="$.split_result",
            retry_on_service_exceptions=True
        )
        split_document.add_retry(
            errors=[sfn.Errors.TIMEOUT, sfn.Errors.TASKS_FAILED],
            interval=Duration.seconds(2),
            max_attempts=2,
            backoff_rate=2.0
        )
        split_document.add_catch(
            handle_error_chain,
            errors=["States.ALL"],
            result_path="$.error"
        )
        
        # ===== UNIFIED WORKFLOW (always uses Map state, even for single documents) =====
        
        # Unified analyze structure (handles both chunk and document)
        # Always stores result in S3, returns only S3 reference
        analyze_structure = tasks.LambdaInvoke(
            self, "AnalyzeStructure",
            lambda_function=self.analyze_structure_fn,
            payload_response_only=True,
            result_path="$.structure_result",
            retry_on_service_exceptions=True,
            payload=sfn.TaskInput.from_object({
                "chunk_s3_key": sfn.JsonPath.string_at("$.chunk_s3_key"),  # From chunk item
                "document_s3_key": sfn.JsonPath.string_at("$.document_s3_key"),  # For single docs (fallback)
                "bucket_name": sfn.JsonPath.string_at("$.bucket_name"),
                "knowledge_base_id": sfn.JsonPath.string_at("$.knowledge_base_id"),
                "region": sfn.JsonPath.string_at("$.region"),
                "chunk_num": sfn.JsonPath.number_at("$.chunk_num"),
                "total_chunks": sfn.JsonPath.number_at("$.total_chunks"),
                "start_char": sfn.JsonPath.number_at("$.start_char"),
                "end_char": sfn.JsonPath.number_at("$.end_char"),
                "job_id": sfn.JsonPath.string_at("$.job_id"),
                "session_id": sfn.JsonPath.string_at("$.session_id"),
                "timestamp": sfn.JsonPath.string_at("$.timestamp")
            })
        )
        analyze_structure.add_retry(
            errors=[sfn.Errors.TIMEOUT, sfn.Errors.TASKS_FAILED],
            interval=Duration.seconds(2),
            max_attempts=2,
            backoff_rate=2.0
        )
        # Note: No catch block here - errors handled at Map state level to avoid CDK recursion issues
        
        # Retrieve all KB queries in single lambda
        # Loads structure results from S3, retrieves queries, stores KB results in S3
        retrieve_all_kb_queries = tasks.LambdaInvoke(
            self, "RetrieveAllKBQueries",
            lambda_function=self.retrieve_all_kb_queries_fn,
            payload_response_only=True,
            result_path="$.kb_retrieval_result",
            retry_on_service_exceptions=True,
            payload=sfn.TaskInput.from_object({
                "structure_s3_key": sfn.JsonPath.string_at("$.structure_result.structure_s3_key"),  # Load from S3
                "knowledge_base_id": sfn.JsonPath.string_at("$.knowledge_base_id"),
                "region": sfn.JsonPath.string_at("$.region"),
                "job_id": sfn.JsonPath.string_at("$.job_id"),
                "session_id": sfn.JsonPath.string_at("$.session_id"),
                "bucket_name": sfn.JsonPath.string_at("$.bucket_name"),
                "chunk_num": sfn.JsonPath.number_at("$.chunk_num")  # Pass chunk_num to avoid S3 overwrites
            })
        )
        retrieve_all_kb_queries.add_retry(
            errors=[sfn.Errors.TIMEOUT, sfn.Errors.TASKS_FAILED],
            interval=Duration.seconds(2),
            max_attempts=2,
            backoff_rate=2.0
        )
        # Note: No catch block here - errors handled at Map state level to avoid CDK recursion issues
        
        # Unified identify conflicts (handles both chunk and document)
        # Always stores result in S3, returns only S3 reference
        identify_conflicts = tasks.LambdaInvoke(
            self, "IdentifyConflicts",
            lambda_function=self.identify_conflicts_fn,
            payload_response_only=True,
            result_path="$.analysis_result",  # Contains S3 reference
            retry_on_service_exceptions=True,
            payload=sfn.TaskInput.from_object({
                "chunk_s3_key": sfn.JsonPath.string_at("$.chunk_s3_key"),  # From chunk item
                "document_s3_key": sfn.JsonPath.string_at("$.document_s3_key"),  # For single docs (fallback)
                "bucket_name": sfn.JsonPath.string_at("$.bucket_name"),
                "knowledge_base_id": sfn.JsonPath.string_at("$.knowledge_base_id"),
                "region": sfn.JsonPath.string_at("$.region"),
                "kb_results_s3_key": sfn.JsonPath.string_at("$.kb_retrieval_result.results_s3_key"),  # From S3
                "chunk_num": sfn.JsonPath.number_at("$.chunk_num"),
                "total_chunks": sfn.JsonPath.number_at("$.total_chunks"),
                "start_char": sfn.JsonPath.number_at("$.start_char"),
                "end_char": sfn.JsonPath.number_at("$.end_char"),
                "job_id": sfn.JsonPath.string_at("$.job_id"),
                "session_id": sfn.JsonPath.string_at("$.session_id"),
                "timestamp": sfn.JsonPath.string_at("$.timestamp")
            })
        )
        identify_conflicts.add_retry(
            errors=[sfn.Errors.TIMEOUT, sfn.Errors.TASKS_FAILED],
            interval=Duration.seconds(2),
            max_attempts=2,
            backoff_rate=2.0
        )
        # Note: No catch block here - errors handled at Map state level to avoid CDK recursion issues
        
        # Unified workflow: structure -> retrieve all queries -> identify conflicts
        unified_workflow = analyze_structure.next(
            retrieve_all_kb_queries.next(identify_conflicts)
        )
        
        # Process all chunks in parallel using unified workflow
        # Works for both single documents (1 chunk) and multiple chunks
        # Use itemSelector to pass both chunk item AND parent context to each iteration
        analyze_chunks_map = sfn.Map(
            self, "AnalyzeChunksParallel",
            items_path="$.split_result.chunks",  # Always has at least 1 chunk (even for single docs)
            max_concurrency=10,
            result_path="$.chunk_analyses",
            item_selector={
                # Chunk-specific data (from the iterated item)
                "chunk_s3_key.$": "$$.Map.Item.Value.s3_key",
                "chunk_num.$": "$$.Map.Item.Value.chunk_num",
                "start_char.$": "$$.Map.Item.Value.start_char",
                "end_char.$": "$$.Map.Item.Value.end_char",
                # Context from parent state (preserved)
                "bucket_name.$": "$.split_result.bucket_name",
                "total_chunks.$": "$.split_result.chunk_count",
                "job_id.$": "$.job_id",
                "session_id.$": "$.session_id",
                "user_id.$": "$.user_id",
                "document_s3_key.$": "$.document_s3_key",
                "terms_profile.$": "$.terms_profile",
                "knowledge_base_id.$": "$.knowledge_base_id",
                "region.$": "$.region",
                "timestamp.$": "$.timestamp"
            }
        )
        
        # Set item processor first
        analyze_chunks_map.item_processor(unified_workflow)
        
        # Add error handling at Map level (best practice per AWS docs)
        # Errors from item processor will be caught here and handled by HandleError Lambda
        analyze_chunks_map.add_catch(
            handle_error_chain,
            errors=["States.ALL"],
            result_path="$.error"
        )
        
        # Merge chunk results - loads individual chunk results from S3
        merge_chunk_results = tasks.LambdaInvoke(
            self, "MergeChunkResults",
            lambda_function=self.merge_chunk_results_fn,
            payload_response_only=True,
            result_path="$.conflicts_result",
            retry_on_service_exceptions=True,
            payload=sfn.TaskInput.from_object({
                "chunk_results": sfn.JsonPath.object_at("$.chunk_analyses"),  # Pass chunk references (contain S3 keys)
                "bucket_name": sfn.JsonPath.string_at("$.split_result.bucket_name"),
                "job_id": sfn.JsonPath.string_at("$.job_id"),
                "session_id": sfn.JsonPath.string_at("$.session_id"),
                "timestamp": sfn.JsonPath.string_at("$.timestamp")
            })
        )
        merge_chunk_results.add_retry(
            errors=[sfn.Errors.TIMEOUT, sfn.Errors.TASKS_FAILED],
            interval=Duration.seconds(2),
            max_attempts=2,
            backoff_rate=2.0
        )
        merge_chunk_results.add_catch(
            handle_error_chain,
            errors=["States.ALL"],
            result_path="$.error"
        )
        
        # ===== COMMON FINAL STEPS =====
        
        # Generate redline
        # CRITICAL: Load conflicts from S3 (merge_chunk_results stores in S3)
        generate_redline = tasks.LambdaInvoke(
            self, "GenerateRedline",
            lambda_function=self.generate_redline_fn,
            payload_response_only=True,
            result_path="$.redline_result",
            retry_on_service_exceptions=True,
            payload=sfn.TaskInput.from_object({
                "conflicts_s3_key": sfn.JsonPath.string_at("$.conflicts_result.conflicts_s3_key"),  # S3 reference from merge
                "conflicts_result": sfn.JsonPath.object_at("$.conflicts_result"),  # Fallback support
                "document_s3_key": sfn.JsonPath.string_at("$.document_s3_key"),
                "bucket_name": sfn.JsonPath.string_at("$.split_result.bucket_name"),  # For loading conflicts from S3
                "bucket_type": sfn.JsonPath.string_at("$.bucket_type"),  # Pass bucket_type for correct S3 bucket lookup
                "session_id": sfn.JsonPath.string_at("$.session_id"),
                "user_id": sfn.JsonPath.string_at("$.user_id"),
                "job_id": sfn.JsonPath.string_at("$.job_id"),
                "timestamp": sfn.JsonPath.string_at("$.timestamp")
            })
        )
        generate_redline.add_retry(
            errors=[sfn.Errors.TIMEOUT, sfn.Errors.TASKS_FAILED],
            interval=Duration.seconds(2),
            max_attempts=2,
            backoff_rate=2.0
        )
        
        # Save results
        # CRITICAL: Load conflicts from S3 (merge_chunk_results stores in S3)
        save_results = tasks.LambdaInvoke(
            self, "SaveResults",
            lambda_function=self.save_results_fn,
            payload_response_only=True,
            result_path="$.save_result",
            retry_on_service_exceptions=True,
            payload=sfn.TaskInput.from_object({
                "conflicts_s3_key": sfn.JsonPath.string_at("$.conflicts_result.conflicts_s3_key"),  # S3 reference from merge
                "analysis_json": sfn.JsonPath.object_at("$.conflicts_result"),  # Fallback support
                "bucket_name": sfn.JsonPath.string_at("$.split_result.bucket_name"),  # For loading conflicts from S3
                "document_s3_key": sfn.JsonPath.string_at("$.document_s3_key"),
                "redlined_s3_key": sfn.JsonPath.string_at("$.redline_result.redlined_document_s3_key"),  # From generate_redline
                "bucket_type": sfn.JsonPath.string_at("$.bucket_type"),  # Pass bucket_type for correct bucket lookup
                "session_id": sfn.JsonPath.string_at("$.session_id"),
                "user_id": sfn.JsonPath.string_at("$.user_id"),
                "job_id": sfn.JsonPath.string_at("$.job_id"),
                "timestamp": sfn.JsonPath.string_at("$.timestamp")
            })
        )
        save_results.add_retry(
            errors=[sfn.Errors.TIMEOUT, sfn.Errors.TASKS_FAILED],
            interval=Duration.seconds(2),
            max_attempts=2,
            backoff_rate=2.0
        )
        
        # Define workflow - always uses Map state (works for both single and multiple chunks)
        processing_path = analyze_chunks_map.next(merge_chunk_results)
        
        # Add error handling to individual states (not chains)
        # All catch blocks use handle_error_chain to ensure cleanup runs after errors
        generate_redline.add_catch(
            handle_error_chain,
            errors=["States.ALL"],
            result_path="$.error"
        )
        save_results.add_catch(
            handle_error_chain,
            errors=["States.ALL"],
            result_path="$.error"
        )
        # If cleanup_session fails, just call handle_error (not handle_error_chain)
        # to avoid infinite recursion - cleanup is best-effort anyway
        cleanup_session.add_catch(
            handle_error,
            errors=["States.ALL"],
            result_path="$.error"
        )
        
        # Final steps
        final_steps = generate_redline.next(save_results).next(cleanup_session)
        
        # Complete workflow definition
        # Always uses Map state - works for single documents (1 chunk) and multiple chunks
        # split_document always creates chunks array with at least 1 chunk
        definition = initialize_job.next(
            split_document.next(
                processing_path.next(final_steps)
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
    
    def create_start_workflow_lambda(self):
        """
        Create the wrapper Lambda that starts the workflow.
        This is the entry point from API Gateway - it generates job_id upfront
        and returns it immediately so frontend can poll for results.
        """
        
        # Create role for the start workflow Lambda
        role = self.iam_roles.create_agent_role(
            "StartWorkflow",
            self.buckets,
            self.analysis_table,
            self.opensearch_collection
        )
        
        # Grant permission to start Step Functions execution
        self.state_machine.grant_start_execution(role)
        
        # Grant permission to update sessions table (for updating session title with document filename)
        role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=[
                    "dynamodb:UpdateItem",
                    "dynamodb:GetItem"
                ],
                resources=[
                    f"arn:aws:dynamodb:{Stack.of(self).region}:{Stack.of(self).account}:table/{self._stack_name}-sessions"
                ]
            )
        )
        
        # Environment variables
        env = {
            "ANALYSES_TABLE_NAME": self.analysis_table.table_name,
            "STATE_MACHINE_ARN": self.state_machine.state_machine_arn,
            "REGION": Stack.of(self).region,
            "KNOWLEDGE_BASE_ID": self.knowledge_base_id,  # Required for passing to Step Functions
            "SESSIONS_TABLE": f"{self._stack_name}-sessions",  # Sessions table name (matches knowledge_management construct)
            "LOG_LEVEL": "INFO"
        }
        
        # Create the Lambda
        self.start_workflow_fn = self._create_lambda(
            "StartWorkflow",
            "start_workflow/lambda_function.lambda_handler",
            role,
            env,
            timeout=Duration.minutes(2),
            memory_size=512
        )
        
        # Create job status Lambda for progress polling
        self.create_job_status_lambda()
    
    def create_job_status_lambda(self):
        """
        Create the Lambda that returns job status for frontend polling.
        This provides real-time progress updates to the user.
        """
        
        # Create role for the job status Lambda (read-only access to DynamoDB)
        role = iam.Role(
            self, "JobStatusRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AWSLambdaBasicExecutionRole"
                )
            ]
        )
        
        # Grant read access to DynamoDB
        self.analysis_table.grant_read_data(role)
        
        # Grant permission to describe Step Functions executions
        # This allows job_status Lambda to check execution status
        role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=[
                    'states:DescribeExecution'
                ],
                resources=[self.state_machine.state_machine_arn + '/*']  # All executions of this state machine
            )
        )
        
        # Grant write access to DynamoDB for updating failed status
        self.analysis_table.grant_write_data(role)
        
        # Environment variables
        env = {
            "ANALYSES_TABLE_NAME": self.analysis_table.table_name,
            "REGION": Stack.of(self).region,
            "LOG_LEVEL": "INFO"
        }
        
        # Create the Lambda
        self.job_status_fn = self._create_lambda(
            "JobStatus",
            "job_status/lambda_function.lambda_handler",
            role,
            env,
            timeout=Duration.minutes(2),
            memory_size=256
        )
    
    def update_knowledge_base_id(self, knowledge_base_id: str):
        """Update all Lambda functions with the real Knowledge Base ID."""
        for func in self.lambda_functions:
            func.add_environment("KNOWLEDGE_BASE_ID", knowledge_base_id)

    def update_knowledge_base_name(self, knowledge_base_name: str):
        """Update all Lambda functions with the Knowledge Base Name."""
        for func in self.lambda_functions:
            func.add_environment("KNOWLEDGE_BASE_NAME", knowledge_base_name)

