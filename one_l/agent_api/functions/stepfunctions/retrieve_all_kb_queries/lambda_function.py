"""
Retrieve all KB queries Lambda function.
Retrieves all queries in a single lambda using concurrent.futures.
Stores all results in a single S3 file.
"""

import json
import boto3
import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from agent_api.agent.prompts.models import KBQueryResult
from agent_api.agent.tools import retrieve_from_knowledge_base

logger = logging.getLogger()
logger.setLevel(logging.INFO)

s3_client = boto3.client('s3')

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

def retrieve_single_query(query_data, knowledge_base_id, region):
    """
    Retrieve a single KB query.
    
    Args:
        query_data: Dict with query, query_id, max_results, section
        knowledge_base_id: Knowledge Base ID
        region: AWS region
        
    Returns:
        KBQueryResult dict
    """
    query = query_data.get('query', '')
    query_id_raw = query_data.get('query_id')
    query_id = int(query_id_raw) if query_id_raw is not None and str(query_id_raw).strip() else 0
    max_results_raw = query_data.get('max_results')
    max_results = int(max_results_raw) if max_results_raw is not None and str(max_results_raw).strip() else 50
    
    try:
        section = query_data.get('section')
        logger.info(f"KB_QUERY_START: query_id={query_id}, section='{section}', query_length={len(query)}, max_results={max_results}, query_preview='{query[:80]}...'")
        
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
                logger.warning(f"KB_QUERY_ERROR: query_id={query_id}, section='{section}', error='{error}'")
            else:
                results = result.get('results', [])
                if not results and 'retrievalResults' in result:
                    results = result.get('retrievalResults', [])
        elif isinstance(result, list):
            results = result
        
        results_count = len(results) if results else 0
        
        # Log detailed KB query results for consistency tracking
        if results_count > 0:
            # Extract unique document sources
            document_sources = set()
            for r in results:
                if isinstance(r, dict):
                    source = r.get('source') or r.get('metadata', {}).get('source') or 'unknown'
                    document_sources.add(source)
            
            logger.info(f"KB_QUERY_SUCCESS: query_id={query_id}, section='{section}', results_count={results_count}, documents_found={len(document_sources)}, documents={list(document_sources)[:5]}{'...' if len(document_sources) > 5 else ''}")
            
            # Log document types found
            doc_types = {}
            for source in document_sources:
                if 'IT Terms' in source or 'Updated' in source:
                    doc_types['IT_Terms'] = doc_types.get('IT_Terms', 0) + 1
                elif 'Standard_Contract' in source or 'Standard Contract' in source:
                    doc_types['Standard_Contract'] = doc_types.get('Standard_Contract', 0) + 1
                elif 'RFR' in source:
                    doc_types['RFR'] = doc_types.get('RFR', 0) + 1
                elif source.startswith('IS.'):
                    doc_types['IS_Standards'] = doc_types.get('IS_Standards', 0) + 1
                elif source.startswith('ISP.'):
                    doc_types['ISP_Policies'] = doc_types.get('ISP_Policies', 0) + 1
                elif 'Exhibit' in source:
                    doc_types['Exhibits'] = doc_types.get('Exhibits', 0) + 1
                else:
                    doc_types['Other'] = doc_types.get('Other', 0) + 1
            
            logger.info(f"KB_QUERY_DOC_TYPES: query_id={query_id}, section='{section}', doc_types={doc_types}")
        else:
            logger.warning(f"KB_QUERY_NO_RESULTS: query_id={query_id}, section='{section}', query='{query[:100]}...', reason='No KB results found for this query'")
        
        return {
            'query_id': query_id,
            'query': query,
            'section': section,  # Preserve section to help identify which vendor section this query targets
            'results': results,
            'success': success,
            'error': error,
            'results_count': results_count
        }
        
    except Exception as e:
        logger.error(f"Error retrieving query {query_id}: {e}")
        return {
            'query_id': query_id,
            'query': query,
            'results': [],
            'success': False,
            'error': str(e),
            'results_count': 0
        }

def lambda_handler(event, context):
    """
    Retrieve all KB queries and store results in S3.
    
    Args:
        event: Lambda event with:
            - structure_s3_key: S3 key with structure results (contains queries array)
            - knowledge_base_id: Knowledge Base ID
            - region: AWS region
            - job_id: Job ID for S3 storage
            - session_id: Session ID for S3 storage
            - bucket_name: S3 bucket for storage
        
    Returns:
        Dict with results_s3_key, results_count, queries_count, success_count, failed_count
    """
    try:
        structure_s3_key = event.get('structure_s3_key')
        bucket_name = event.get('bucket_name') or os.environ.get('AGENT_PROCESSING_BUCKET')
        knowledge_base_id = event.get('knowledge_base_id') or os.environ.get('KNOWLEDGE_BASE_ID')
        
        # CRITICAL: Load structure results from S3 (analyze_structure stores in S3)
        if not structure_s3_key or not bucket_name:
            raise ValueError("structure_s3_key and bucket_name are required")
        
        try:
            structure_response = s3_client.get_object(Bucket=bucket_name, Key=structure_s3_key)
            structure_json = structure_response['Body'].read().decode('utf-8')
            structure_data = json.loads(structure_json)
            queries = structure_data.get('queries', [])
            logger.info(f"Loaded structure results from S3: {structure_s3_key}, found {len(queries)} queries")
        except Exception as e:
            logger.error(f"CRITICAL: Failed to load structure results from S3 {structure_s3_key}: {e}")
            raise  # Fail fast - structure results must be in S3
        
        # Fallback to name lookup
        if (not knowledge_base_id or knowledge_base_id == "placeholder") and os.environ.get('KNOWLEDGE_BASE_NAME'):
            knowledge_base_id = get_kb_id_by_name(os.environ.get('KNOWLEDGE_BASE_NAME'))
        
        region = event.get('region') or os.environ.get('REGION')
        job_id = event.get('job_id', 'unknown')
        session_id = event.get('session_id', 'unknown')
        bucket_name = event.get('bucket_name') or os.environ.get('AGENT_PROCESSING_BUCKET')
        chunk_num = event.get('chunk_num', 0)  # Get chunk number to avoid overwrites
        
        if not queries:
            raise ValueError("queries array is required")
        
        if not knowledge_base_id or not region:
            raise ValueError("knowledge_base_id and region are required")
        
        if not bucket_name:
            raise ValueError("bucket_name is required for S3 storage")
        
        logger.info(f"Retrieving {len(queries)} KB queries for job {job_id}, chunk {chunk_num}")
        
        # Retrieve all queries in parallel using ThreadPoolExecutor
        # Use max_workers=20 to match previous parallel map concurrency
        all_results = []
        success_count = 0
        failed_count = 0
        
        with ThreadPoolExecutor(max_workers=20) as executor:
            # Submit all queries
            future_to_query = {
                executor.submit(retrieve_single_query, query_data, knowledge_base_id, region): query_data
                for query_data in queries
            }
            
            # Collect results as they complete
            for future in as_completed(future_to_query):
                query_data = future_to_query[future]
                try:
                    result = future.result()
                    all_results.append(result)
                    if result.get('success'):
                        success_count += 1
                    else:
                        failed_count += 1
                        logger.warning(f"Query {result.get('query_id')} failed: {result.get('error')}")
                except Exception as e:
                    failed_count += 1
                    logger.error(f"Exception retrieving query {query_data.get('query_id', 'unknown')}: {e}")
                    all_results.append({
                        'query_id': query_data.get('query_id', 0),
                        'query': query_data.get('query', ''),
                        'section': query_data.get('section'),  # Preserve section even on error
                        'results': [],
                        'success': False,
                        'error': str(e),
                        'results_count': 0
                    })
        
        # Sort results by query_id to maintain order
        all_results.sort(key=lambda x: x.get('query_id', 0))
        
        # POST-PROCESSING: Add fallback queries for major sections missing Terms and Conditions documents
        # Major contract sections from Massachusetts Terms and Conditions document
        major_sections = [
            'INDEMNITY', 'INDEMNIFICATION', 'LIABILITY', 'LIMITATION', 'LIMITATIONS',
            'TERMINATION', 'SUSPENSION', 'FORCE MAJEURE',
            'PAYMENT', 'PAYMENTS', 'COMPENSATION', 'CONTRACTOR PAYMENTS',
            'WARRANTY', 'WARRANTIES', 'NON-INFRINGEMENT',
            'CONFIDENTIALITY', 'PRIVACY', 'DATA', 'PROTECTION',
            'ASSIGNMENT', 'TRANSFER',
            'NOTICE', 'NOTICES', 'WRITTEN NOTICE',
            'SUBCONTRACTING', 'SUBCONTRACTOR',
            'INSURANCE',
            'RECORD', 'RETENTION', 'INSPECTION', 'AUDIT',
            'RISK OF LOSS',
            'WAIVER', 'WAIVERS',
            'GOVERNING LAW', 'FORUM', 'JURISDICTION', 'MEDIATION', 'CHOICE OF LAW',
            'SEVERABILITY', 'INTEGRATION', 'BOILERPLATE', 'CONFLICTS',
            'AFFIRMATIVE ACTION', 'NON-DISCRIMINATION',
            'PRESS RELEASE', 'MARKETING', 'PUBLICITY',
            'AI USAGE', 'DISCLOSURE'
        ]
        fallback_queries = []
        max_query_id = max([r.get('query_id', 0) for r in all_results], default=0)
        
        for result in all_results:
            section = result.get('section', '').upper()
            results = result.get('results', [])
            
            # Check if this is a major section query
            is_major_section = any(keyword in section for keyword in major_sections)
            
            if is_major_section and results:
                # Check if Terms and Conditions documents are in results
                has_terms_doc = False
                for r in results:
                    source = r.get('source', '') or r.get('metadata', {}).get('source', '')
                    if 'terms' in source.lower() or 'conditions' in source.lower():
                        has_terms_doc = True
                        break
                
                # If major section query has results but no Terms docs, add fallback
                if not has_terms_doc:
                    max_query_id += 1
                    # Create fallback query specifically targeting Terms and Conditions
                    # Extract the main section keyword(s) from the section name
                    section_keywords = [kw for kw in major_sections if kw in section]
                    if not section_keywords:
                        # Fallback: use first word of section if no keyword matches
                        section_keywords = [section.split()[0]] if section else ['Terms']
                    
                    # Create focused fallback query targeting Terms and Conditions document
                    fallback_query = f"{' '.join(section_keywords)} Terms and Conditions Massachusetts Commonwealth document requirements"
                    
                    logger.info(f"KB_FALLBACK_QUERY: Adding fallback query {max_query_id} for section '{result.get('section')}' - original query had {len(results)} results but no Terms and Conditions documents")
                    
                    fallback_queries.append({
                        'query_id': max_query_id,
                        'query': fallback_query,
                        'section': result.get('section', '') + ' (Fallback)',
                        'is_fallback': True,
                        'original_query_id': result.get('query_id')
                    })
        
        # Execute fallback queries if any were created
        if fallback_queries:
            logger.info(f"KB_FALLBACK_EXECUTION: Executing {len(fallback_queries)} fallback queries for Terms and Conditions documents")
            with ThreadPoolExecutor(max_workers=20) as executor:
                fallback_futures = {
                    executor.submit(retrieve_single_query, query_data, knowledge_base_id, region): query_data
                    for query_data in fallback_queries
                }
                
                for future in as_completed(fallback_futures):
                    query_data = fallback_futures[future]
                    try:
                        fallback_result = future.result()
                        all_results.append(fallback_result)
                        if fallback_result.get('success') and fallback_result.get('results_count', 0) > 0:
                            logger.info(f"KB_FALLBACK_SUCCESS: Fallback query {fallback_result.get('query_id')} returned {fallback_result.get('results_count')} results")
                        else:
                            logger.warning(f"KB_FALLBACK_NO_RESULTS: Fallback query {fallback_result.get('query_id')} returned no results")
                    except Exception as e:
                        logger.error(f"KB_FALLBACK_ERROR: Exception executing fallback query {query_data.get('query_id', 'unknown')}: {e}")
            
            # Re-sort after adding fallback results
            all_results.sort(key=lambda x: x.get('query_id', 0))
        
        # Calculate total results count and analyze query effectiveness
        total_results_count = sum(r.get('results_count', 0) for r in all_results)
        queries_with_results = [r for r in all_results if r.get('results_count', 0) > 0]
        queries_without_results = [r for r in all_results if r.get('results_count', 0) == 0]
        
        # Log comprehensive KB retrieval summary
        query_success_rate = len(queries_with_results) / len(all_results) * 100 if all_results else 0
        logger.info(f"KB_RETRIEVAL_SUMMARY: total_queries={len(all_results)}, queries_with_results={len(queries_with_results)}, queries_without_results={len(queries_without_results)}, success_rate={query_success_rate:.1f}%, total_kb_results={total_results_count}")
        
        # Log all document types found across all queries
        all_document_sources = set()
        for r in queries_with_results:
            for result in r.get('results', []):
                if isinstance(result, dict):
                    source = result.get('source') or result.get('metadata', {}).get('source') or 'unknown'
                    all_document_sources.add(source)
        
        logger.info(f"KB_RETRIEVAL_DOCUMENTS: total_unique_documents={len(all_document_sources)}, documents={sorted(list(all_document_sources))[:10]}{'...' if len(all_document_sources) > 10 else ''}")
        
        # Log queries with no results for debugging
        if queries_without_results:
            logger.warning(f"KB_RETRIEVAL_NO_RESULTS: {len(queries_without_results)} queries returned no results:")
            for q in queries_without_results[:10]:  # Log first 10 for brevity
                logger.warning(f"KB_QUERY_NO_RESULTS_DETAIL: query_id={q.get('query_id')}, section='{q.get('section', 'N/A')}', query='{q.get('query', '')[:100]}...'")
            if len(queries_without_results) > 10:
                logger.warning(f"KB_QUERY_NO_RESULTS_DETAIL: ... and {len(queries_without_results) - 10} more queries with no results")
        
        # Warn if success rate is low
        if query_success_rate < 50:
            logger.warning(f"KB_RETRIEVAL_WARNING: Low success rate ({query_success_rate:.1f}%). Consider improving query generation to include more Massachusetts-specific terminology.")
        
        # Store all results in S3 - include chunk_num to avoid overwrites when chunks run in parallel
        s3_key = f"{session_id}/kb_results/{job_id}_chunk_{chunk_num}_all_queries.json"
        results_json = json.dumps(all_results)
        results_size = len(results_json.encode('utf-8'))
        
        try:
            s3_client.put_object(
                Bucket=bucket_name,
                Key=s3_key,
                Body=results_json.encode('utf-8'),
                ContentType='application/json'
            )
            logger.info(f"Stored {len(all_results)} KB query results ({total_results_count} total results, {results_size} bytes) in S3: {s3_key}")
        except Exception as s3_error:
            logger.error(f"CRITICAL: Failed to store KB results in S3: {s3_error}")
            raise  # Fail fast if S3 storage fails
        
        # CRITICAL: Only return S3 reference, never return actual data
        # Step Functions has 256KB limit - always store in S3 and return only reference
        return {
            'results_s3_key': s3_key,
            'results_count': total_results_count,
            'queries_count': len(queries),
            'success_count': success_count,
            'failed_count': failed_count
            # DO NOT include 'queries' array - data is in S3 only
        }
        
    except Exception as e:
        logger.error(f"Error in retrieve_all_kb_queries: {e}")
        raise

