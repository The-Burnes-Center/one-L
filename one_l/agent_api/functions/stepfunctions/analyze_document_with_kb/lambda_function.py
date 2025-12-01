"""
Analyze document with KB Lambda function.
Same as analyze_chunk_with_kb for single document.
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
    Analyze document with KB results for conflict detection.
    
    Args:
        event: Lambda event with document_s3_key, bucket_name, knowledge_base_id, region, kb_results
        context: Lambda context
        
    Returns:
        ConflictDetectionOutput with explanation and conflicts
    """
    try:
        document_s3_key = event.get('document_s3_key')
        bucket_name = event.get('bucket_name')
        knowledge_base_id = event.get('knowledge_base_id') or os.environ.get('KNOWLEDGE_BASE_ID')
        region = event.get('region') or os.environ.get('REGION')
        kb_results = event.get('kb_results', [])
        
        if not document_s3_key or not bucket_name:
            raise ValueError("document_s3_key and bucket_name are required")
        
        # Load document from S3
        response = s3_client.get_object(Bucket=bucket_name, Key=document_s3_key)
        document_data = response['Body'].read()
        
        # Create Model instance
        model = Model(knowledge_base_id, region)
        
        # Format KB results as context
        kb_context = ""
        if kb_results:
            kb_context = "\n\nKnowledge Base Results:\n"
            for idx, kb_result in enumerate(kb_results):
                if isinstance(kb_result, dict):
                    kb_context += f"\nResult {idx + 1}:\n"
                    kb_context += f"Document: {kb_result.get('document', {}).get('title', 'Unknown')}\n"
                    kb_context += f"Content: {kb_result.get('content', {}).get('text', '')[:500]}...\n"
        
        # Determine document format
        is_pdf = document_s3_key.lower().endswith('.pdf')
        doc_format = 'pdf' if is_pdf else 'docx'
        filename = os.path.basename(document_s3_key)
        # Sanitize filename for Bedrock Converse API requirements
        sanitized_filename = model._sanitize_filename_for_converse(filename)
        
        # Prepare messages with document and KB context
        messages = [
            {
                "role": "user",
                "content": [
                    {
                        "text": f"{CONFLICT_DETECTION_PROMPT}{kb_context}"
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
        logger.info("Calling Claude for document conflict detection")
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
        logger.error(f"Error in analyze_document_with_kb: {e}")
        raise

