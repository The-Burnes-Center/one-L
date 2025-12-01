"""
Analyze chunk with KB Lambda function.
Loads chunk from S3, calls Claude with CONFLICT_DETECTION_PROMPT, validates with Pydantic.
"""

import json
import boto3
import logging
import os
import io
from agent_api.agent.prompts.conflict_detection_prompt import CONFLICT_DETECTION_PROMPT
from agent_api.agent.prompts.models import ConflictDetectionOutput
from agent_api.agent.model import Model, _extract_json_only
from pydantic import ValidationError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

s3_client = boto3.client('s3')

# Import progress tracker
try:
    from shared.progress_tracker import update_progress
except ImportError:
    update_progress = None

def lambda_handler(event, context):
    """
    Analyze chunk with KB results for conflict detection.
    
    Args:
        event: Lambda event with chunk_s3_key, bucket_name, knowledge_base_id, region, 
               chunk_num, total_chunks, start_char, end_char, kb_results
        context: Lambda context
        
    Returns:
        ConflictDetectionOutput with explanation and conflicts
    """
    try:
        chunk_s3_key = event.get('chunk_s3_key')
        bucket_name = event.get('bucket_name')
        knowledge_base_id = event.get('knowledge_base_id') or os.environ.get('KNOWLEDGE_BASE_ID')
        region = event.get('region') or os.environ.get('REGION')
        chunk_num = event.get('chunk_num', 0)
        total_chunks = event.get('total_chunks', 1)
        start_char = event.get('start_char', 0)
        end_char = event.get('end_char', 0)
        kb_results = event.get('kb_results', [])
        
        if not chunk_s3_key or not bucket_name:
            raise ValueError("chunk_s3_key and bucket_name are required")
        
        # Load chunk from S3
        response = s3_client.get_object(Bucket=bucket_name, Key=chunk_s3_key)
        chunk_data = response['Body'].read()
        
        # Create Model instance
        model = Model(knowledge_base_id, region)
        
        # Prepare chunk context
        chunk_context = f"You are analyzing chunk {chunk_num + 1} of {total_chunks} (characters {start_char}-{end_char})"
        
        # Format KB results as context
        # CRITICAL: KB results may be stored in S3 per-query (if they exceeded size limits)
        # Load from S3 if results_s3_key is present, otherwise use inline results
        kb_context = ""
        if kb_results:
            kb_context = "\n\nKnowledge Base Results:\n"
            for idx, kb_result in enumerate(kb_results):
                if isinstance(kb_result, dict):
                    # CRITICAL: KB results are always stored in S3
                    # Load from S3 using results_s3_key
                    results_s3_key = kb_result.get('results_s3_key')
                    results_count = kb_result.get('results_count', 0)
                    
                    if results_s3_key:
                        # Load results from S3 (always the case now)
                        try:
                            kb_response = s3_client.get_object(Bucket=bucket_name, Key=results_s3_key)
                            kb_results_json = kb_response['Body'].read().decode('utf-8')
                            results = json.loads(kb_results_json)
                            logger.info(f"Loaded {results_count} KB results for query {kb_result.get('query_id', idx)} from S3: {results_s3_key}")
                        except Exception as e:
                            logger.error(f"CRITICAL: Failed to load KB results from S3 {results_s3_key}: {e}")
                            raise  # Fail fast - KB results must be in S3
                    elif results_count > 0:
                        # Fallback: inline results (shouldn't happen, but handle for backward compatibility)
                        logger.warning(f"Query {kb_result.get('query_id', idx)} has {results_count} results but no S3 key - using inline results")
                        results = kb_result.get('results', [])
                    else:
                        # No results for this query
                        continue
                    
                    # Format results for context
                    if results:
                        for result_idx, result in enumerate(results[:10]):  # Limit to first 10 results per query
                            kb_context += f"\nQuery {idx + 1}, Result {result_idx + 1}:\n"
                            if isinstance(result, dict):
                                kb_context += f"Document: {result.get('document', {}).get('title', 'Unknown')}\n"
                                kb_context += f"Content: {result.get('content', {}).get('text', '')[:500]}...\n"
        
        # Prepare messages with chunk document and KB context
        from docx import Document
        doc = Document(io.BytesIO(chunk_data))
        filename = os.path.basename(chunk_s3_key)
        # Sanitize filename for Bedrock Converse API requirements
        sanitized_filename = model._sanitize_filename_for_converse(filename)
        
        messages = [
            {
                "role": "user",
                "content": [
                    {
                        "text": f"{chunk_context}. {CONFLICT_DETECTION_PROMPT}{kb_context}"
                    },
                    {
                        "document": {
                            "format": "docx",
                            "name": sanitized_filename,
                            "source": {
                                "bytes": chunk_data
                            }
                        }
                    }
                ]
            }
        ]
        
        # Call Claude with conflict detection prompt
        logger.info(f"Calling Claude for chunk {chunk_num + 1} conflict detection")
        response = model._call_claude_with_tools(messages)
        
        # Extract content
        content = ""
        if response.get("output", {}).get("message", {}).get("content"):
            for content_block in response["output"]["message"]["content"]:
                if content_block.get("text"):
                    content += content_block["text"]
        
        # Extract JSON
        response_json = _extract_json_only(content)
        
        # Validate with Pydantic
        try:
            validated_output = ConflictDetectionOutput.model_validate_json(response_json)
            logger.info(f"Pydantic validation successful: {len(validated_output.conflicts)} conflicts")
        except ValidationError as e:
            logger.error(f"Pydantic validation failed: {e.errors()}")
            raise ValueError(f"Invalid response structure: {e}")
        
        # Update progress (for chunk processing)
        job_id = event.get('job_id')
        timestamp = event.get('timestamp')
        chunk_num = event.get('chunk_num', 0)
        total_chunks = event.get('total_chunks', 1)
        if update_progress and job_id and timestamp:
            # Calculate progress based on chunk number
            base_progress = 30  # processing_chunks stage
            chunk_progress = int((chunk_num + 1) / total_chunks * 10)  # 30-40% range
            update_progress(
                job_id, timestamp, 'processing_chunks',
                f'Analyzing chunk {chunk_num + 1} of {total_chunks}, found {len(validated_output.conflicts)} conflicts...'
            )
        
        # CRITICAL: Store result in S3 if it's large to avoid Step Functions payload limits
        # When multiple chunks run in parallel, their results are collected into $.chunk_analyses
        # This can easily exceed 256KB if there are many chunks with many conflicts
        # CRITICAL: Always store result in S3 to avoid Step Functions payload size limits
        # Always storing in S3 ensures consistency, easier cleanup, and predictable behavior
        result_dict = validated_output.model_dump()
        job_id = event.get('job_id', 'unknown')
        session_id = event.get('session_id', 'unknown')
        chunk_num = event.get('chunk_num', 0)
        
        try:
            s3_key = f"{session_id}/chunk_results/{job_id}_chunk_{chunk_num}_analysis.json"
            result_json = json.dumps(result_dict)
            result_size = len(result_json.encode('utf-8'))
            
            s3_client.put_object(
                Bucket=bucket_name,
                Key=s3_key,
                Body=result_json.encode('utf-8'),
                ContentType='application/json'
            )
            
            logger.info(f"Stored chunk {chunk_num} analysis result ({result_size} bytes) in S3: {s3_key}")
            
            # Always return S3 key reference (results are always in S3)
            return {
                'chunk_num': chunk_num,
                'results_s3_key': s3_key,
                'conflicts_count': len(validated_output.conflicts),
                'has_results': True
            }
        except Exception as s3_error:
            logger.error(f"CRITICAL: Failed to store chunk {chunk_num} result in S3: {s3_error}")
            raise  # Fail fast if S3 storage fails - we need S3 for Step Functions limits
        
    except Exception as e:
        logger.error(f"Error in analyze_chunk_with_kb: {e}")
        raise

