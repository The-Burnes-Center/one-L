"""
Retrieve KB query Lambda function.
Wraps existing retrieve_from_knowledge_base function and validates output.
Stores large results in S3 to avoid Step Functions payload size limits.
"""

import json
import boto3
import logging
import os
from agent_api.agent.prompts.models import KBQueryResult
from agent_api.agent.tools import retrieve_from_knowledge_base

logger = logging.getLogger()
logger.setLevel(logging.INFO)

s3_client = boto3.client('s3')

# CRITICAL: Always store results in S3 to avoid Step Functions payload size limits
# Step Functions has a 256KB limit, and Map states collect all results
# Always storing in S3 ensures consistency, easier cleanup, and predictable behavior

def get_kb_id_by_name(name: str) -> str:
    """Resolve Knowledge Base ID from Name."""
    try:
        client = boto3.client('bedrock-agent')
        paginator = client.get_paginator('list_knowledge_bases')
        for page in paginator.paginate(MaxResults=100):
            for kb in page.get('knowledgeBaseSummaries', []):
                if kb.get('name') == name:
                    logger.info(f"Resolved Knowledge Base ID {kb.get('knowledgeBaseId')} for name {name}")
                    return kb.get('knowledgeBaseId')
        logger.warning(f"Knowledge Base with name {name} not found")
    except Exception as e:
        logger.error(f"Error resolving KB ID for name {name}: {e}")
    return None

def lambda_handler(event, context):
    """
    Execute single KB query and return validated result.
    
    Args:
        event: Lambda event with query, query_id, max_results, knowledge_base_id, region
        context: Lambda context
        
    Returns:
        KBQueryResult with query_id, query, results, success, error
    """
    try:
        query = event.get('query')
        # Handle query_id - can be None, string, or int from Step Functions
        query_id_raw = event.get('query_id')
        query_id = int(query_id_raw) if query_id_raw is not None and str(query_id_raw).strip() else 0
        # Handle max_results - can be None, string, or int from Step Functions
        max_results_raw = event.get('max_results')
        max_results = int(max_results_raw) if max_results_raw is not None and str(max_results_raw).strip() else 50
        knowledge_base_id = event.get('knowledge_base_id') or os.environ.get('KNOWLEDGE_BASE_ID')
        
        # Fallback to name lookup
        if (not knowledge_base_id or knowledge_base_id == "placeholder") and os.environ.get('KNOWLEDGE_BASE_NAME'):
             knowledge_base_id = get_kb_id_by_name(os.environ.get('KNOWLEDGE_BASE_NAME'))
             
        region = event.get('region') or os.environ.get('REGION')
        
        if not query:
            raise ValueError("query is required")
        
        if not knowledge_base_id or not region:
            raise ValueError("knowledge_base_id and region are required")
        
        # Execute KB query
        logger.info(f"Executing KB query {query_id}: {query[:50]}...")
        result = retrieve_from_knowledge_base(
            query=query,
            max_results=max_results,
            knowledge_base_id=knowledge_base_id,
            region=region
        )
        
        # Extract results from response
        results = []
        success = True
        error = None
        
        if isinstance(result, dict):
            if 'error' in result:
                success = False
                error = result.get('error')
            else:
                # Extract results array
                results = result.get('results', [])
                if not results and 'retrievalResults' in result:
                    results = result.get('retrievalResults', [])
        elif isinstance(result, list):
            results = result
        
        # CRITICAL: Always store results in S3 to avoid Step Functions payload size limits
        # Step Functions has a 256KB limit, and Map states collect all results
        # Always store in S3 for consistency, easier cleanup, and predictable behavior
        results_s3_key = None
        results_count = len(results) if results else 0
        
        if results:
            # Always store results in S3
            job_id = event.get('job_id', 'unknown')
            session_id = event.get('session_id', 'unknown')
            bucket_name = event.get('bucket_name') or os.environ.get('AGENT_PROCESSING_BUCKET')
            
            if not bucket_name:
                logger.warning(f"No bucket_name provided for query {query_id}, cannot store in S3")
            else:
                try:
                    s3_key = f"{session_id}/kb_results/{job_id}_query_{query_id}_results.json"
                    results_json = json.dumps(results)
                    results_size = len(results_json.encode('utf-8'))
                    
                    s3_client.put_object(
                        Bucket=bucket_name,
                        Key=s3_key,
                        Body=results_json.encode('utf-8'),
                        ContentType='application/json'
                    )
                    
                    results_s3_key = s3_key
                    logger.info(f"Stored {results_count} KB results ({results_size} bytes) in S3 for query {query_id}: {s3_key}")
                except Exception as s3_error:
                    logger.error(f"CRITICAL: Failed to store results in S3 for query {query_id}: {s3_error}")
                    raise  # Fail fast if S3 storage fails - we need S3 for Step Functions limits
        
        # Create validated output
        # Always return empty results array and S3 key (results are always in S3)
        output = KBQueryResult(
            query_id=query_id,
            query=query,
            results=[],  # Always empty - results are in S3
            success=success,
            error=error
        )
        
        output_dict = output.model_dump()
        
        # Always add S3 key (if results exist)
        if results_s3_key:
            output_dict['results_s3_key'] = results_s3_key
            output_dict['results_count'] = results_count
            logger.info(f"KB query {query_id} completed: {results_count} results stored in S3")
        elif results_count > 0:
            # This shouldn't happen, but handle gracefully
            logger.warning(f"KB query {query_id} has {results_count} results but no S3 key - returning inline (may exceed limits)")
            output_dict['results'] = results  # Fallback to inline if S3 failed
        else:
            logger.info(f"KB query {query_id} completed: no results")
        
        # Return plain result
        return output_dict
        
    except Exception as e:
        logger.error(f"Error in retrieve_kb_query: {e}")
        # Return error result
        output = KBQueryResult(
            query_id=event.get('query_id', 0),
            query=event.get('query', ''),
            results=[],
            success=False,
            error=str(e)
        )
        return output.model_dump()

