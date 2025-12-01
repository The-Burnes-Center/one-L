"""
Analyze chunk structure Lambda function.
Loads chunk from S3, calls Claude with STRUCTURE_ANALYSIS_PROMPT, validates with Pydantic.
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

def lambda_handler(event, context):
    """
    Analyze chunk structure and generate queries.
    
    Args:
        event: Lambda event with chunk_s3_key, bucket_name, knowledge_base_id, region, chunk_num, total_chunks, start_char, end_char
        context: Lambda context
        
    Returns:
        StructureAnalysisOutput with queries and chunk_structure
    """
    try:
        chunk_s3_key = event.get('chunk_s3_key')
        bucket_name = event.get('bucket_name')
        knowledge_base_id = event.get('knowledge_base_id')
        region = event.get('region')
        chunk_num = event.get('chunk_num', 0)
        total_chunks = event.get('total_chunks', 1)
        start_char = event.get('start_char', 0)
        end_char = event.get('end_char', 0)
        
        if not chunk_s3_key or not bucket_name:
            raise ValueError("chunk_s3_key and bucket_name are required")
        
        # Load chunk from S3
        response = s3_client.get_object(Bucket=bucket_name, Key=chunk_s3_key)
        chunk_data = response['Body'].read()
        
        # Create Model instance
        model = Model(knowledge_base_id, region)
        
        # Prepare chunk context
        chunk_context = f"You are analyzing chunk {chunk_num + 1} of {total_chunks} (characters {start_char}-{end_char})"
        
        # Prepare messages with chunk document
        from docx import Document
        doc = Document(io.BytesIO(chunk_data))
        filename = os.path.basename(chunk_s3_key)
        
        messages = [
            {
                "role": "user",
                "content": [
                    {
                        "text": f"{chunk_context}. {STRUCTURE_ANALYSIS_PROMPT}"
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
        
        # Call Claude with structure analysis prompt
        logger.info(f"Calling Claude for chunk {chunk_num + 1} structure analysis")
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
        
        # Return plain result (Step Functions uses result_path to merge with state)
        return validated_output.model_dump()
        
    except Exception as e:
        logger.error(f"Error in analyze_chunk_structure: {e}")
        raise

