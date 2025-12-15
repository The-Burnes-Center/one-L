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
            
            logger.info(f"CONFLICT_DETECTION_KB_LOADED: Loaded KB results from S3: {kb_results_s3_key}, found {len(kb_results)} query results")
            
            # Log detailed KB results summary for conflict detection
            queries_with_results = [r for r in kb_results if isinstance(r, dict) and r.get('results_count', 0) > 0]
            queries_without_results = [r for r in kb_results if isinstance(r, dict) and r.get('results_count', 0) == 0]
            total_kb_results = sum(r.get('results_count', 0) for r in kb_results if isinstance(r, dict))
            
            logger.info(f"CONFLICT_DETECTION_KB_SUMMARY: total_queries={len(kb_results)}, queries_with_results={len(queries_with_results)}, queries_without_results={len(queries_without_results)}, total_kb_results={total_kb_results}")
            
            # Log all documents available for citation
            all_available_documents = set()
            for r in queries_with_results:
                for result in r.get('results', []):
                    if isinstance(result, dict):
                        source = result.get('source') or result.get('metadata', {}).get('source') or 'unknown'
                        all_available_documents.add(source)
            
            logger.info(f"CONFLICT_DETECTION_AVAILABLE_DOCS: {len(all_available_documents)} unique documents available for citation: {sorted(list(all_available_documents))[:15]}{'...' if len(all_available_documents) > 15 else ''}")
            
            # Log KB query details for each query with results
            for r in queries_with_results[:10]:  # Log first 10 queries
                query_id = r.get('query_id', 'unknown')
                section = r.get('section', 'N/A')
                results_count = r.get('results_count', 0)
                documents = set()
                for result in r.get('results', []):
                    if isinstance(result, dict):
                        source = result.get('source') or result.get('metadata', {}).get('source') or 'unknown'
                        documents.add(source)
                logger.info(f"CONFLICT_DETECTION_KB_QUERY: query_id={query_id}, section='{section}', results={results_count}, documents={sorted(list(documents))[:3]}{'...' if len(documents) > 3 else ''}")
            
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
            # First, collect all unique document names for a summary
            all_doc_names = set()
            for kb_result in kb_results:
                if isinstance(kb_result, dict):
                    for result in kb_result.get('results', []):
                        if isinstance(result, dict):
                            doc_name = result.get('source', 'Unknown')
                            if doc_name != 'Unknown':
                                all_doc_names.add(doc_name)
            
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
                            # Make document name prominent
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
            total_conflicts = len(validated_output.conflicts)
            logger.info(f"CONFLICT_DETECTION_VALIDATION: Pydantic validation successful: {total_conflicts} conflicts detected")
            
            # Log detailed conflict analysis for consistency tracking
            conflicts_with_docs = [c for c in validated_output.conflicts if c.source_doc != "N/A – Not tied to a specific Massachusetts clause"]
            conflicts_without_docs = [c for c in validated_output.conflicts if c.source_doc == "N/A – Not tied to a specific Massachusetts clause"]
            
            citation_rate = (len(conflicts_with_docs) / total_conflicts * 100) if total_conflicts > 0 else 0
            logger.info(f"CONFLICT_DETECTION_SUMMARY: total_conflicts={total_conflicts}, conflicts_with_docs={len(conflicts_with_docs)}, conflicts_without_docs={len(conflicts_without_docs)}, citation_rate={citation_rate:.1f}%")
            
            # Log conflict type distribution
            conflict_types = {}
            for c in validated_output.conflicts:
                conflict_types[c.conflict_type] = conflict_types.get(c.conflict_type, 0) + 1
            logger.info(f"CONFLICT_DETECTION_TYPES: {conflict_types}")
            
            # Log document citation distribution
            doc_citations = {}
            for c in conflicts_with_docs:
                doc_citations[c.source_doc] = doc_citations.get(c.source_doc, 0) + 1
            logger.info(f"CONFLICT_DETECTION_DOC_CITATIONS: {doc_citations}")
            
            # Log detailed analysis for conflicts WITHOUT document citations
            logger.info(f"CONFLICT_DETECTION_NA_ANALYSIS: Analyzing {len(conflicts_without_docs)} conflicts marked as N/A")
            for idx, conflict in enumerate(conflicts_without_docs[:10]):  # Log first 10 N/A conflicts
                conflict_id = conflict.clarification_id
                conflict_type = conflict.conflict_type
                summary = conflict.summary
                vendor_quote_preview = conflict.vendor_quote[:150] if conflict.vendor_quote else ''
                
                logger.info(f"CONFLICT_NA_DETAIL: conflict_id={conflict_id}, type={conflict_type}, summary='{summary}', vendor_quote='{vendor_quote_preview}...'")
                
                # Check if any KB queries should have matched this conflict
                matching_queries = []
                for kb_result in kb_results:
                    if isinstance(kb_result, dict) and kb_result.get('results_count', 0) > 0:
                        query_text = kb_result.get('query', '').lower()
                        section = kb_result.get('section', '')
                        # Check if query topic matches conflict topic
                        conflict_keywords = [w.lower() for w in summary.split()[:5]]  # First 5 words
                        if any(kw in query_text for kw in conflict_keywords if len(kw) > 3):
                            matching_queries.append({
                                'query_id': kb_result.get('query_id'),
                                'section': section,
                                'results_count': kb_result.get('results_count', 0),
                                'documents': [r.get('source') or r.get('metadata', {}).get('source', 'unknown') 
                                             for r in kb_result.get('results', [])[:3]]
                            })
                
                if matching_queries:
                    logger.warning(f"CONFLICT_NA_MISSING_CITATION: conflict_id={conflict_id}, type={conflict_type}, found {len(matching_queries)} potentially matching KB queries but marked as N/A")
                    for mq in matching_queries[:3]:
                        logger.warning(f"CONFLICT_NA_MATCHING_QUERY: query_id={mq['query_id']}, section='{mq['section']}', results={mq['results_count']}, documents={mq['documents']}")
                else:
                    logger.info(f"CONFLICT_NA_NO_MATCH: conflict_id={conflict_id}, type={conflict_type}, no matching KB queries found - correctly marked as N/A")
            
            # Log conflicts WITH document citations for verification
            logger.info(f"CONFLICT_DETECTION_DOC_ANALYSIS: Analyzing {len(conflicts_with_docs)} conflicts with document citations")
            for idx, conflict in enumerate(conflicts_with_docs[:10]):  # Log first 10 with citations
                conflict_id = conflict.clarification_id
                conflict_type = conflict.conflict_type
                source_doc = conflict.source_doc
                summary = conflict.summary
                
                logger.info(f"CONFLICT_DOC_DETAIL: conflict_id={conflict_id}, type={conflict_type}, source_doc='{source_doc}', summary='{summary}'")
                
                # Verify the cited document was actually in KB results
                doc_found_in_kb = False
                for kb_result in kb_results:
                    if isinstance(kb_result, dict):
                        for result in kb_result.get('results', []):
                            if isinstance(result, dict):
                                result_source = result.get('source') or result.get('metadata', {}).get('source', '')
                                if source_doc in result_source or result_source in source_doc:
                                    doc_found_in_kb = True
                                    logger.info(f"CONFLICT_DOC_VERIFIED: conflict_id={conflict_id}, source_doc='{source_doc}' found in KB query {kb_result.get('query_id')} results")
                                    break
                        if doc_found_in_kb:
                            break
                
                if not doc_found_in_kb:
                    logger.warning(f"CONFLICT_DOC_NOT_FOUND: conflict_id={conflict_id}, source_doc='{source_doc}' not found in any KB query results - citation may be incorrect")
            
        except ValidationError as e:
            logger.error(f"CONFLICT_DETECTION_VALIDATION_ERROR: Pydantic validation failed: {e.errors()}")
            # Log the problematic JSON for debugging
            logger.error(f"CONFLICT_DETECTION_JSON_ERROR: Problematic JSON (first 1000 chars): {response_json[:1000]}")
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

