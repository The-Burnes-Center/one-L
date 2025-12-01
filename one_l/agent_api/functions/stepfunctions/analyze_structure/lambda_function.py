"""
Unified analyze structure Lambda function.
Handles both chunk and document structure analysis.
Replaces analyze_chunk_structure and analyze_document_structure.
"""

import json
import boto3
import logging
import os
import io
from agent_api.agent.prompts.structure_analysis_prompt import STRUCTURE_ANALYSIS_PROMPT
from agent_api.agent.prompts.models import StructureAnalysisOutput
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
    Analyze structure and generate queries for chunk or document.
    
    Args:
        event: Lambda event with:
            - chunk_s3_key OR document_s3_key (one required)
            - bucket_name (required)
            - knowledge_base_id (required)
            - region (required)
            - chunk_num (optional, 0-indexed)
            - total_chunks (optional)
            - start_char (optional)
            - end_char (optional)
            - job_id, timestamp (for progress tracking)
        
    Returns:
        StructureAnalysisOutput with queries and structure metadata
    """
    try:
        chunk_s3_key = event.get('chunk_s3_key')
        document_s3_key = event.get('document_s3_key')
        bucket_name = event.get('bucket_name')
        knowledge_base_id = event.get('knowledge_base_id')
        region = event.get('region')
        chunk_num = event.get('chunk_num', 0)
        total_chunks = event.get('total_chunks', 1)
        start_char = event.get('start_char', 0)
        end_char = event.get('end_char', 0)
        
        # Determine which S3 key to use
        s3_key = chunk_s3_key or document_s3_key
        is_chunk = chunk_s3_key is not None
        
        if not s3_key or not bucket_name:
            raise ValueError("Either chunk_s3_key or document_s3_key, and bucket_name are required")
        
        # Load document/chunk from S3
        response = s3_client.get_object(Bucket=bucket_name, Key=s3_key)
        document_data = response['Body'].read()
        
        # Create Model instance
        model = Model(knowledge_base_id, region)
        
        # Determine document format
        is_pdf = s3_key.lower().endswith('.pdf')
        doc_format = 'pdf' if is_pdf else 'docx'
        filename = os.path.basename(s3_key)
        sanitized_filename = model._sanitize_filename_for_converse(filename)
        
        # Prepare context - always include chunk context if chunk_num/total_chunks provided
        # For single documents: chunk_num=0, total_chunks=1
        # Always pass chunk context when chunk_num and total_chunks are available
        if total_chunks is not None and total_chunks >= 1:
            if is_chunk and total_chunks > 1:
                chunk_context = f"You are analyzing chunk {chunk_num + 1} of {total_chunks} (characters {start_char}-{end_char})"
            else:
                # Single document: chunk_num=0, total_chunks=1
                chunk_context = f"You are analyzing document (chunk {chunk_num + 1} of {total_chunks})"
            prompt_text = f"{chunk_context}. {STRUCTURE_ANALYSIS_PROMPT}"
        else:
            # chunk_num/total_chunks not provided (backward compatibility)
            prompt_text = STRUCTURE_ANALYSIS_PROMPT
        
        # Prepare messages with document
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
        
        # Call Claude with structure analysis prompt
        if is_chunk:
            logger.info(f"Calling Claude for chunk {chunk_num + 1} structure analysis")
        else:
            logger.info("Calling Claude for document structure analysis")
        
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
            validated_output = StructureAnalysisOutput.model_validate_json(response_json)
            logger.info(f"Pydantic validation successful: {len(validated_output.queries)} queries")
        except ValidationError as e:
            logger.error(f"Pydantic validation failed: {e.errors()}")
            raise ValueError(f"Invalid response structure: {e}")
        
        # Update progress
        job_id = event.get('job_id')
        timestamp = event.get('timestamp')
        if update_progress and job_id and timestamp:
            if is_chunk and total_chunks > 1:
                update_progress(
                    job_id, timestamp, 'analyzing',
                    f'Analyzing chunk {chunk_num + 1} of {total_chunks}, generated {len(validated_output.queries)} queries...'
                )
            else:
                update_progress(
                    job_id, timestamp, 'analyzing',
                    f'Analyzed document structure, generated {len(validated_output.queries)} queries for knowledge base lookup...'
                )
        
        # Return plain result (Step Functions uses result_path to merge with state)
        return validated_output.model_dump()
        
    except Exception as e:
        logger.error(f"Error in analyze_structure: {e}")
        raise

