"""
Retrieve KB query Lambda function.
Wraps existing retrieve_from_knowledge_base function and validates output.
"""

import json
import boto3
import logging
import os
from agent_api.agent.prompts.models import KBQueryResult
from agent_api.agent.tools import retrieve_from_knowledge_base

logger = logging.getLogger()
logger.setLevel(logging.INFO)

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
        query_id = event.get('query_id', 0)
        max_results = event.get('max_results', 50)
        knowledge_base_id = event.get('knowledge_base_id') or os.environ.get('KNOWLEDGE_BASE_ID')
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
        
        # Create validated output
        output = KBQueryResult(
            query_id=query_id,
            query=query,
            results=results,
            success=success,
            error=error
        )
        
        logger.info(f"KB query {query_id} completed: {len(results)} results")
        
        return {
            "statusCode": 200,
            "body": output.model_dump_json()
        }
        
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
        return {
            "statusCode": 200,
            "body": output.model_dump_json()
        }

