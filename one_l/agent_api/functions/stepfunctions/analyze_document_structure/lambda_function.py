"""
Analyze document structure Lambda function.
Same as analyze_chunk_structure for single document (non-chunked).
"""

import json
import boto3
import logging
import os
import io
from agent_api.agent.prompts.structure_analysis_prompt import STRUCTURE_ANALYSIS_PROMPT
from agent_api.agent.prompts.models import StructureAnalysisOutput, ValidationError
from agent_api.agent.model import Model, _extract_json_only

logger = logging.getLogger()
logger.setLevel(logging.INFO)

s3_client = boto3.client('s3')

def lambda_handler(event, context):
    """
    Analyze document structure and generate queries for single document.
    
    Args:
        event: Lambda event with document_s3_key, bucket_name, knowledge_base_id, region
        context: Lambda context
        
    Returns:
        StructureAnalysisOutput with queries and chunk_structure
    """
    try:
        document_s3_key = event.get('document_s3_key')
        bucket_name = event.get('bucket_name')
        knowledge_base_id = event.get('knowledge_base_id')
        region = event.get('region')
        
        if not document_s3_key or not bucket_name:
            raise ValueError("document_s3_key and bucket_name are required")
        
        # Load document from S3
        response = s3_client.get_object(Bucket=bucket_name, Key=document_s3_key)
        document_data = response['Body'].read()
        
        # Create Model instance
        model = Model(knowledge_base_id, region)
        
        # Determine document format
        is_pdf = document_s3_key.lower().endswith('.pdf')
        doc_format = 'pdf' if is_pdf else 'docx'
        filename = os.path.basename(document_s3_key)
        
        # Prepare messages with document
        messages = [
            {
                "role": "user",
                "content": [
                    {
                        "text": STRUCTURE_ANALYSIS_PROMPT
                    },
                    {
                        "document": {
                            "format": doc_format,
                            "name": filename,
                            "source": {
                                "bytes": document_data
                            }
                        }
                    }
                ]
            }
        ]
        
        # Call Claude with structure analysis prompt
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
        
        return {
            "statusCode": 200,
            "body": validated_output.model_dump_json()
        }
        
    except Exception as e:
        logger.error(f"Error in analyze_document_structure: {e}")
        raise

