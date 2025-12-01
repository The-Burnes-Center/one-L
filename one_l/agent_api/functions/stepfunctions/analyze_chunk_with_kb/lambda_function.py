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
        kb_context = ""
        if kb_results:
            kb_context = "\n\nKnowledge Base Results:\n"
            for idx, kb_result in enumerate(kb_results):
                if isinstance(kb_result, dict):
                    kb_context += f"\nResult {idx + 1}:\n"
                    kb_context += f"Document: {kb_result.get('document', {}).get('title', 'Unknown')}\n"
                    kb_context += f"Content: {kb_result.get('content', {}).get('text', '')[:500]}...\n"
        
        # Prepare messages with chunk document and KB context
        from docx import Document
        doc = Document(io.BytesIO(chunk_data))
        filename = os.path.basename(chunk_s3_key)
        
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
                            "name": filename,
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
        
        # Return plain result
        return validated_output.model_dump()
        
    except Exception as e:
        logger.error(f"Error in analyze_chunk_with_kb: {e}")
        raise

