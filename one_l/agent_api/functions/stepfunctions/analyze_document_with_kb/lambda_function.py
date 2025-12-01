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

# Import progress tracker
try:
    from shared.progress_tracker import update_progress
except ImportError:
    update_progress = None

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
        kb_results_s3_key = event.get('kb_results_s3_key')  # S3 key for large KB results
        
        if not document_s3_key or not bucket_name:
            raise ValueError("document_s3_key and bucket_name are required")
        
        # Load KB results from S3 if provided (for large results that exceed payload limits)
        if kb_results_s3_key:
            try:
                kb_response = s3_client.get_object(Bucket=bucket_name, Key=kb_results_s3_key)
                kb_results_json = kb_response['Body'].read().decode('utf-8')
                kb_results = json.loads(kb_results_json)
                logger.info(f"Loaded {len(kb_results)} KB results from S3: {kb_results_s3_key}")
            except Exception as e:
                logger.warning(f"Failed to load KB results from S3 {kb_results_s3_key}: {e}, using in-memory results")
        
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
                    # Handle both direct results and wrapped results
                    result_data = kb_result.get('results', [kb_result]) if 'results' in kb_result else [kb_result]
                    for result_idx, result in enumerate(result_data[:10]):  # Limit to first 10 results per query
                        kb_context += f"\nResult {idx + 1}.{result_idx + 1}:\n"
                        if isinstance(result, dict):
                            kb_context += f"Document: {result.get('document', {}).get('title', 'Unknown')}\n"
                            kb_context += f"Content: {result.get('content', {}).get('text', '')[:500]}...\n"
        
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
        
        # Update progress
        job_id = event.get('job_id')
        timestamp = event.get('timestamp')
        if update_progress and job_id and timestamp:
            update_progress(
                job_id, timestamp, 'identifying_conflicts',
                f'Identified {len(validated_output.conflicts)} conflicts in document...'
            )
        
        # Return plain result
        return validated_output.model_dump()
        
    except Exception as e:
        logger.error(f"Error in analyze_document_with_kb: {e}")
        raise

