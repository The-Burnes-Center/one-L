"""
Unified analyze with KB Lambda function.
Handles both chunk and document analysis with KB results.
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
    Analyze chunk or document with KB results for conflict detection.
    
    Args:
        event: Lambda event with:
            - chunk_s3_key OR document_s3_key (one required)
            - bucket_name (required)
            - knowledge_base_id (required)
            - region (required)
            - kb_results_s3_key (required) - S3 key with all KB query results
            - chunk_num (optional, 0-indexed)
            - total_chunks (optional)
            - start_char (optional)
            - end_char (optional)
            - job_id, timestamp (for progress tracking)
        
    Returns:
        Dict with chunk_num, results_s3_key, conflicts_count, has_results (always stores in S3)
    """
    try:
        chunk_s3_key = event.get('chunk_s3_key')
        document_s3_key = event.get('document_s3_key')
        bucket_name = event.get('bucket_name')
        knowledge_base_id = event.get('knowledge_base_id') or os.environ.get('KNOWLEDGE_BASE_ID')
        region = event.get('region') or os.environ.get('REGION')
        kb_results_s3_key = event.get('kb_results_s3_key')
        chunk_num = event.get('chunk_num', 0)
        total_chunks = event.get('total_chunks', 1)
        start_char = event.get('start_char', 0)
        end_char = event.get('end_char', 0)
        
        # Determine which S3 key to use
        s3_key = chunk_s3_key or document_s3_key
        is_chunk = chunk_s3_key is not None
        
        if not s3_key or not bucket_name:
            raise ValueError("Either chunk_s3_key or document_s3_key, and bucket_name are required")
        
        if not kb_results_s3_key:
            raise ValueError("kb_results_s3_key is required")
        
        # Load KB results from S3
        try:
            kb_response = s3_client.get_object(Bucket=bucket_name, Key=kb_results_s3_key)
            kb_results_json = kb_response['Body'].read().decode('utf-8')
            kb_results_raw = json.loads(kb_results_json)
            # KB results are stored as a list by retrieve_all_kb_queries
            # Handle both list format and dict format
            if isinstance(kb_results_raw, list):
                kb_results = kb_results_raw
            elif isinstance(kb_results_raw, dict) and 'all_results' in kb_results_raw:
                kb_results = kb_results_raw['all_results']
            else:
                # Fallback: wrap in list if it's a single dict
                kb_results = [kb_results_raw] if isinstance(kb_results_raw, dict) else []
            logger.info(f"Loaded KB results from S3: {kb_results_s3_key}, found {len(kb_results)} query results")
        except Exception as e:
            logger.error(f"CRITICAL: Failed to load KB results from S3 {kb_results_s3_key}: {e}")
            raise  # Fail fast - KB results must be in S3
        
        # Load document/chunk from S3
        response = s3_client.get_object(Bucket=bucket_name, Key=s3_key)
        document_data = response['Body'].read()
        
        # Create Model instance
        model = Model(knowledge_base_id, region)
        
        # Document format - only DOCX supported
        doc_format = 'docx'
        filename = os.path.basename(s3_key)
        sanitized_filename = model._sanitize_filename_for_converse(filename)
        
        # Format KB results as context
        kb_context = ""
        if kb_results:
            kb_context = "\n\nKnowledge Base Results:\n"
            for idx, kb_result in enumerate(kb_results):
                if isinstance(kb_result, dict):
                    results = kb_result.get('results', [])
                    query_id = kb_result.get('query_id', idx)
                    query = kb_result.get('query', '')
                    section = kb_result.get('section')  # Get section to identify which vendor section this query targets
                    
                    if not results:
                        continue
                    
                    # Format results for context - include section to help AI correlate KB results to vendor sections
                    if section:
                        kb_context += f"\nQuery {idx + 1} (ID: {query_id}, Target Section: {section}):\n{query}\n"
                    else:
                        kb_context += f"\nQuery {idx + 1} (ID: {query_id}):\n{query}\n"
                    
                    for result_idx, result in enumerate(results[:10]):  # Limit to first 10 results per query to reduce token usage
                        kb_context += f"\n  Result {result_idx + 1}:\n"
                        if isinstance(result, dict):
                            # Extract document name from 'source' field (extracted by _extract_source_from_result)
                            document_name = result.get('source', 'Unknown')
                            # Extract content from 'text' field
                            content_text = result.get('text', '')
                            kb_context += f"    Document: {document_name}\n"
                            if content_text:
                                kb_context += f"    Content: {content_text[:500]}...\n"
                            else:
                                kb_context += f"    Content: (empty)\n"
        
        # Prepare chunk context - always include if chunk_num/total_chunks provided
        # For single documents: chunk_num=0, total_chunks=1
        # Always pass chunk context when chunk_num and total_chunks are available
        if total_chunks is not None and total_chunks >= 1:
            if is_chunk and total_chunks > 1:
                chunk_context = f"You are analyzing chunk {chunk_num + 1} of {total_chunks} (characters {start_char}-{end_char})"
            else:
                # Single document: chunk_num=0, total_chunks=1
                chunk_context = f"You are analyzing document (chunk {chunk_num + 1} of {total_chunks})"
            prompt_text = f"{chunk_context}. {CONFLICT_DETECTION_PROMPT}{kb_context}"
        else:
            # chunk_num/total_chunks not provided (backward compatibility)
            prompt_text = f"{CONFLICT_DETECTION_PROMPT}{kb_context}"
        
        # Prepare messages with document and KB context
        messages = [
            {
                "role": "user",
                "content": [
                    {
                        "text": prompt_text
                    },
                    {
                        "document": {
                            "format": doc_format,
                            "name": sanitized_filename,
                            "source": {
                                "bytes": document_data
                            }
                        }
                    }
                ]
            }
        ]
        
        # Call Claude with conflict detection prompt
        # Use _call_claude_without_tools since KB results are already pre-loaded in the prompt
        if is_chunk:
            logger.info(f"Calling Claude for chunk {chunk_num + 1} conflict detection (KB results pre-loaded in prompt)")
        else:
            logger.info("Calling Claude for document conflict detection (KB results pre-loaded in prompt)")
        
        response = model._call_claude_without_tools(messages)
        
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
            # Log the problematic JSON for debugging
            logger.error(f"Problematic JSON (first 1000 chars): {response_json[:1000]}")
            raise ValueError(f"Invalid response structure: {e}")
        
        # Update progress
        job_id = event.get('job_id')
        timestamp = event.get('timestamp')
        session_id = event.get('session_id')
        user_id = event.get('user_id')
        if update_progress and job_id and timestamp:
            if is_chunk and total_chunks > 1:
                # Calculate progress based on chunk number
                update_progress(
                    job_id, timestamp, 'processing_chunks',
                    f'Analyzing chunk {chunk_num + 1} of {total_chunks}, found {len(validated_output.conflicts)} conflicts...',
                    session_id=session_id,
                    user_id=user_id
                )
            else:
                update_progress(
                    job_id, timestamp, 'identifying_conflicts',
                    f'Identified {len(validated_output.conflicts)} conflicts in document...',
                    session_id=session_id,
                    user_id=user_id
                )
        
        # CRITICAL: Always store result in S3 and return only S3 reference
        # Step Functions has 256KB limit - always store in S3, never return data directly
        result_dict = validated_output.model_dump()
        result_json = json.dumps(result_dict)
        result_size = len(result_json.encode('utf-8'))
        
        try:
            s3_key_result = f"{event.get('session_id', 'unknown')}/chunk_results/{job_id}_chunk_{chunk_num}_analysis.json"
            s3_client.put_object(
                Bucket=bucket_name,
                Key=s3_key_result,
                Body=result_json.encode('utf-8'),
                ContentType='application/json'
            )
            
            logger.info(f"Stored chunk {chunk_num} analysis result ({result_size} bytes) in S3: {s3_key_result}")
            
            # Always return only S3 reference (never return data directly)
            return {
                'chunk_num': chunk_num,
                'results_s3_key': s3_key_result,
                'conflicts_count': len(validated_output.conflicts),
                'has_results': True
            }
        except Exception as s3_error:
            logger.error(f"CRITICAL: Failed to store chunk {chunk_num} result in S3: {s3_error}")
            raise  # Fail fast if S3 storage fails
        
    except Exception as e:
        logger.error(f"Error in identify_conflicts: {e}")
        raise

