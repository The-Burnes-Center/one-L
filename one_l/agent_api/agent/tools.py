"""
Tool functions for document review agent.
Provides knowledge base retrieval and document red-lining capabilities.
"""

import json
import boto3
from botocore.config import Config
import os
import logging
import re
import time
import hashlib
from typing import Dict, Any, List
from collections import defaultdict
from docx import Document
from docx.shared import RGBColor
import io

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Initialize AWS clients with optimized timeout for knowledge base retrieval
# Knowledge base queries are typically fast (under 30 seconds)
# Reference: https://repost.aws/knowledge-center/bedrock-large-model-read-timeouts
bedrock_agent_config = Config(
    read_timeout=120,  # 2 minutes - more than sufficient for knowledge base queries
)
bedrock_agent_client = boto3.client('bedrock-agent-runtime', config=bedrock_agent_config)
s3_client = boto3.client('s3')

# Knowledge base optimization constants - TUNED FOR MAXIMUM CONFLICT DETECTION
MAX_CHUNK_SIZE = 3000  # Increased tokens per chunk for more context
MIN_RELEVANCE_SCORE = 0.5  # Lowered threshold to capture more potentially relevant content
OPTIMAL_RESULTS_PER_QUERY = 50  # Increased results per query for comprehensive coverage
DEDUPLICATION_THRESHOLD = 0.90  # Slightly higher threshold to allow more similar content variations

# Exponential backoff configuration for throttling resilience
MAX_RETRIES = 5
BASE_DELAY = 1.0
MAX_DELAY = 32.0
BACKOFF_MULTIPLIER = 2.0

# Global cache for session-based deduplication
_content_cache = {}
_query_cache = {}

def _calculate_content_signature(text: str) -> str:
    """Calculate semantic signature for deduplication."""
    # Normalize text for consistent comparison
    normalized = re.sub(r'\s+', ' ', text.lower().strip())
    normalized = re.sub(r'[^\w\s]', '', normalized)
    return hashlib.sha256(normalized.encode()).hexdigest()[:16]

def _is_duplicate_content(text: str, threshold: float = DEDUPLICATION_THRESHOLD) -> bool:
    """Check if content is duplicate using semantic similarity."""
    signature = _calculate_content_signature(text)
    
    if signature in _content_cache:
        return True
    
    # Simple similarity check with existing content signatures
    for existing_sig in _content_cache:
        matches = sum(c1 == c2 for c1, c2 in zip(signature, existing_sig) if len(signature) == len(existing_sig))
        if len(existing_sig) > 0 and matches / len(existing_sig) > threshold:
            return True
    
    _content_cache[signature] = text
    return False

def _chunk_content_intelligently(text: str, max_size: int = MAX_CHUNK_SIZE) -> List[str]:
    """Intelligently chunk content preserving semantic boundaries."""
    if len(text) <= max_size * 4:  # Approximate token conversion (4 chars = 1 token)
        return [text]
    
    # Split on semantic boundaries (sentences, then paragraphs)
    sentences = re.split(r'(?<=[.!?])\s+', text)
    chunks = []
    current_chunk = ""
    
    for sentence in sentences:
        if len(current_chunk) + len(sentence) <= max_size * 4:
            current_chunk += sentence + " "
        else:
            if current_chunk.strip():
                chunks.append(current_chunk.strip())
            current_chunk = sentence + " "
    
    if current_chunk.strip():
        chunks.append(current_chunk.strip())
    
    return chunks

def _filter_and_prioritize_results(results: List[Dict], max_results: int) -> List[Dict]:
    """Filter results by relevance and prioritize for optimal context usage."""
    # Filter by minimum relevance score
    filtered_results = [
        r for r in results 
        if r.get('score', 0) >= MIN_RELEVANCE_SCORE
    ]
    
    # Sort by score (descending)
    sorted_results = sorted(
        filtered_results, 
        key=lambda x: x.get('score', 0), 
        reverse=True
    )
    
    # Limit results for optimal performance
    return sorted_results[:min(max_results, OPTIMAL_RESULTS_PER_QUERY)]

def get_tool_definitions() -> List[Dict[str, Any]]:
    """Get tool definitions for Claude in Converse API format."""
    return [
        {
            "toolSpec": {
                "name": "retrieve_from_knowledge_base",
                "description": "Exhaustively retrieve ALL relevant reference documents for conflict detection. Optimized for maximum coverage with deduplication, relevance filtering, and smart chunking. Use 8-12+ targeted queries to ensure no conflicts are missed. Lowered relevance threshold captures edge cases.",
                "inputSchema": {
                    "json": {
                        "type": "object",
                        "properties": {
                            "query": {
                                "type": "string",
                                "description": "Targeted search query to find reference documents. Use specific contract terms, legal phrases, or vendor-specific language. Try variations of important terms to catch all relevant content."
                            },
                            "max_results": {
                                "type": "integer",
                                "description": "Maximum number of results to retrieve (auto-optimized for comprehensive coverage, default: 50)",
                                "default": 50
                            }
                        },
                        "required": ["query"]
                    }
                }
            }
        }
    ]

def retrieve_from_knowledge_base(
    query: str, 
    max_results: int = 50,
    knowledge_base_id: str = None,
    region: str = None
) -> Dict[str, Any]:
    """
    Intelligently retrieve relevant documents from the knowledge base with optimization.
    
    Features:
    - Exponential backoff for throttling resilience
    - Semantic deduplication to prevent duplicate content
    - Intelligent chunking for optimal context usage
    - Relevance filtering to improve quality
    - Performance optimization for multiple queries
    
    Args:
        query: Search query for relevant documents
        max_results: Maximum number of results to return
        knowledge_base_id: Knowledge base ID from environment
        region: AWS region
        
    Returns:
        Dictionary containing optimized retrieved documents and metadata
    """
    
    def _retrieve_with_retry(retry_count: int = 0) -> Dict[str, Any]:
        """Internal function to handle retrieval with exponential backoff."""
        try:

            
            response = bedrock_agent_client.retrieve(
                knowledgeBaseId=knowledge_base_id,
                retrievalQuery={'text': query},
                retrievalConfiguration={
                    'vectorSearchConfiguration': {
                        'numberOfResults': max_results
                    }
                }
            )
            
            return {"success": True, "response": response, "retry_count": retry_count}
            
        except Exception as e:
            error_msg = str(e)
            
            # Check for throttling errors and implement exponential backoff
            if ("ThrottlingException" in error_msg or "Too many tokens" in error_msg or 
                "rate" in error_msg.lower() or "throttl" in error_msg.lower()):
                
                if retry_count < MAX_RETRIES:
                    delay = min(BASE_DELAY * (BACKOFF_MULTIPLIER ** retry_count), MAX_DELAY)

                    time.sleep(delay)
                    return _retrieve_with_retry(retry_count + 1)
                else:

                    return {
                        "success": False,
                        "error": f"Throttling limit exceeded after {MAX_RETRIES} retries",
                        "retry_count": retry_count
                    }
            else:
                # Non-throttling error
                return {"success": False, "error": error_msg, "retry_count": retry_count}
    
    try:
        # Get knowledge base ID from environment if not provided
        if not knowledge_base_id:
            knowledge_base_id = os.environ.get('KNOWLEDGE_BASE_ID')
        
        if not knowledge_base_id:
            return {
                "success": False,
                "error": "Knowledge base ID not available",
                "results": []
            }
        
        # Check query cache to avoid duplicate requests in same session
        query_hash = _calculate_content_signature(query)
        if query_hash in _query_cache:

            cached_result = _query_cache[query_hash].copy()
            cached_result["cached"] = True
            return cached_result
        
        # Execute retrieval with retry logic
        retrieval_result = _retrieve_with_retry()
        
        if not retrieval_result["success"]:
            error_response = {
                "success": False,
                "error": retrieval_result["error"],
                "query": query,
                "results": [],
                "retry_count": retrieval_result.get("retry_count", 0)
            }
            return error_response
        
        response = retrieval_result["response"]
        retry_count = retrieval_result["retry_count"]
        
        # Process and optimize the results
        raw_results = []
        source_documents = set()
        for result in response.get('retrievalResults', []):
            content = result.get('content', {})
            metadata = result.get('metadata', {})
            source = metadata.get('source', 'Unknown')
            source_documents.add(source)
            
            raw_results.append({
                "text": content.get('text', ''),
                "score": result.get('score', 0),
                "source": source,
                "metadata": metadata
            })
        
        # Enhanced logging: Track which reference documents were found
        logger.info(f"KNOWLEDGE_BASE_QUERY: '{query[:100]}...' found {len(raw_results)} results from {len(source_documents)} source documents: {list(source_documents)}")
        
        # Apply intelligent filtering and prioritization
        filtered_results = _filter_and_prioritize_results(raw_results, max_results)
        
        # Process results with chunking and deduplication
        optimized_results = []
        duplicates_filtered = 0
        chunks_created = 0
        
        for result in filtered_results:
            # Check for duplicate content
            if _is_duplicate_content(result["text"]):
                duplicates_filtered += 1
                continue
            
            # Apply intelligent chunking for large content
            chunks = _chunk_content_intelligently(result["text"])
            
            if len(chunks) == 1:
                # Single chunk, add as-is with optimization metadata
                optimized_result = result.copy()
                optimized_result.update({
                    "tokens_estimated": len(result["text"]) // 4,
                    "optimized": True,
                    "chunk_info": {"is_chunked": False, "total_chunks": 1}
                })
                optimized_results.append(optimized_result)
            else:
                # Multiple chunks, add each with metadata
                chunks_created += len(chunks) - 1  # -1 because we count additional chunks
                for i, chunk in enumerate(chunks):
                    chunk_result = result.copy()
                    chunk_result.update({
                        "text": chunk,
                        "tokens_estimated": len(chunk) // 4,
                        "optimized": True,
                        "chunk_info": {
                            "is_chunked": True,
                            "chunk_number": i + 1,
                            "total_chunks": len(chunks),
                            "original_source": result["source"]
                        }
                    })
                    optimized_results.append(chunk_result)
        
        # Generate comprehensive response with optimization statistics
        final_response = {
            "success": True,
            "query": query,
            "results_count": len(optimized_results),
            "results": optimized_results,
            "optimization_stats": {
                "raw_results_retrieved": len(raw_results),
                "filtered_by_relevance": len(raw_results) - len(filtered_results),
                "duplicates_filtered": duplicates_filtered,
                "chunks_created": chunks_created,
                "final_optimized_count": len(optimized_results),
                "retry_count": retry_count,
                "avg_relevance_score": sum(r.get('score', 0) for r in optimized_results) / len(optimized_results) if optimized_results else 0,
                "cache_hit": False
            },
            "performance_metrics": {
                "query_hash": query_hash,
                "processing_successful": True,
                "optimization_ratio": f"{len(optimized_results)}/{len(raw_results)}" if raw_results else "0/0"
            }
        }
        
        # Cache the result for future use in this session
        _query_cache[query_hash] = final_response.copy()
        


        
        return final_response
        
    except Exception as e:
        logger.error(f"Error in optimized knowledge base retrieval: {str(e)}")
        return {
            "success": False,
            "error": str(e),
            "query": query,
            "results": [],
            "optimization_stats": {
                "error_occurred": True,
                "error_type": type(e).__name__
            }
        }

def clear_knowledge_base_cache():
    """
    Clear the session cache for knowledge base retrieval.
    Call this between document review sessions to ensure fresh retrievals.
    """
    global _content_cache, _query_cache
    _content_cache.clear()
    _query_cache.clear()


def get_cache_statistics() -> Dict[str, Any]:
    """
    Get current cache statistics for monitoring and debugging.
    
    Returns:
        Dictionary containing cache usage statistics
    """
    return {
        "content_cache_size": len(_content_cache),
        "query_cache_size": len(_query_cache),
        "cache_memory_usage": {
            "content_signatures": len(_content_cache),
            "cached_queries": len(_query_cache)
        }
    }


def redline_document(
    analysis_data: str,
    document_s3_key: str,
    bucket_type: str = "user_documents",
    session_id: str = None,
    user_id: str = None
) -> Dict[str, Any]:
    """
    Complete redlining workflow: download document, extract content, apply redlining, upload result.
    Handles all document operations internally - no external dependencies.
    
    Args:
        analysis_data: Analysis text containing conflict information
        document_s3_key: S3 key of the original document
        bucket_type: Type of source bucket (user_documents, knowledge, agent_processing)
        session_id: Session ID for organizing output files
        user_id: User ID for organizing output files
        
    Returns:
        Dictionary containing redlined document information and processing results
    """
    
    try:
        logger.info(f"REDLINE_START: Document={document_s3_key}, Analysis={len(analysis_data)} chars")
        # Get bucket configurations
        source_bucket = _get_bucket_name(bucket_type)
        agent_processing_bucket = os.environ.get('AGENT_PROCESSING_BUCKET')
        
        if not agent_processing_bucket or not source_bucket:
            return {
                "success": False,
                "error": "Required buckets not configured"
            }
        

        
        # Step 1: Copy document to agent processing bucket
        agent_document_key = _copy_document_to_processing(document_s3_key, source_bucket, agent_processing_bucket)
        
        # Step 2: Download and load the document
        doc = _download_and_load_document(agent_processing_bucket, agent_document_key)
        
        # Debug logging: Log document structure for troubleshooting
        logger.info(f"DOCUMENT_DEBUG: Loaded document with {len(doc.paragraphs)} paragraphs")
        for i, para in enumerate(doc.paragraphs[:5]):  # Log first 5 paragraphs
            if para.text.strip():
                logger.info(f"DOCUMENT_DEBUG: Para {i}: '{para.text[:100]}...'")
        
        # Step 3: Parse conflicts and create redline items from analysis data
        redline_items = parse_conflicts_for_redlining(analysis_data)
        logger.info(f"REDLINE_PARSE: Found {len(redline_items)} conflicts to redline")
        logger.info(f"REDLINE_PARSE: First conflict preview: '{redline_items[0].get('text', '')[:100]}...'")
        
        if not redline_items:

            return {
                "success": False,
                "error": "No conflicts found in analysis data for redlining"
            }
        
        # Step 4: Apply redlining with exact sentence matching
        logger.info(f"REDLINE_APPLY: Starting redlining - {len(redline_items)} conflicts, {len(doc.paragraphs)} paragraphs")
        
        results = apply_exact_sentence_redlining(doc, redline_items)
        logger.info(f"REDLINE_RESULTS: Matches found: {results['matches_found']}")
        logger.info(f"REDLINE_RESULTS: Failed matches: {len(results.get('failed_matches', []))}")
        logger.info(f"REDLINE_RESULTS: Success rate: {(results['matches_found']/len(redline_items)*100):.1f}%")

        # Step 5: Save and upload redlined document
        redlined_s3_key = _create_redlined_filename(agent_document_key, session_id, user_id)
        upload_success = _save_and_upload_document(doc, agent_processing_bucket, redlined_s3_key, {
            'original_document': document_s3_key,
            'agent_document': agent_document_key,
            'redlined_by': 'Legal-AI',
            'conflicts_count': str(len(redline_items)),
            'matches_found': str(results['matches_found'])
        })
        
        if not upload_success:
            return {
                "success": False,
                "error": "Failed to upload redlined document"
            }
        



        
        # Original redlining completion
        result = {
            "success": True,
            "original_document": document_s3_key,
            "agent_document": agent_document_key,
            "redlined_document": redlined_s3_key,
            "conflicts_processed": len(redline_items),
            "matches_found": results['matches_found'],
            "paragraphs_with_redlines": results['paragraphs_with_redlines'],
            "bucket": agent_processing_bucket,
            "message": f"Successfully created redlined document with {results['matches_found']} conflicts highlighted"
        }

        # NEW: After successful redlining, cleanup session documents
        if session_id and user_id:

            
            try:
                cleanup_result = _cleanup_session_documents(session_id, user_id)
                result["cleanup_performed"] = True
                result["cleanup_result"] = cleanup_result
                
                if cleanup_result.get('success'):
                    pass
                else:
                    pass
                    
            except Exception as cleanup_error:
                # Don't fail redlining if cleanup fails
                logger.error(f"Session cleanup error: {cleanup_error}")
                result["cleanup_performed"] = False
                result["cleanup_error"] = str(cleanup_error)
        else:

            result["cleanup_performed"] = False

        return result
        
    except Exception as e:
        logger.error(f"Error in redlining workflow: {str(e)}")
        return {
            "success": False,
            "error": str(e),
            "original_document": document_s3_key
        }


def parse_conflicts_for_redlining(analysis_data: str) -> List[Dict[str, str]]:
    """
    Parse the markdown table format conflicts data for redlining.
    Extracts exact sentences from Summary column that should be present in vendor document.
    
    Args:
        analysis_data: Analysis string containing markdown table
        
    Returns:
        List of redline items with exact text to match and comments
    """
    logger.info(f"PARSE_START: Analysis data length: {len(analysis_data)} characters")
    logger.info(f"PARSE_START: Analysis preview: {analysis_data[:150]}...")

    redline_items = []
    
    try:
        lines = analysis_data.strip().split('\n')
        
        # Find the table header and data rows
        header_found = False
        
        for line in lines:
            line = line.strip()
            
            # Skip empty lines and markdown formatting
            if not line or line.startswith('|---') or line.startswith('|-'):
                continue
            
            # Check if this is the header row (should contain all expected columns)
            if ('| Clarification ID |' in line and '| Vendor Quote |' in line and 
                '| Summary |' in line and '| Rationale |' in line):
                header_found = True
                continue
            
            # Process data rows
            if header_found and line.startswith('|') and line.endswith('|'):
                # Split by pipe and clean up
                parts = [part.strip() for part in line.split('|')[1:-1]]
                
                if len(parts) >= 7:  # Need 7 columns for the complete table
                    clarification_id = parts[0]
                    vendor_quote = parts[1]  # This contains the exact text from vendor document for redlining
                    summary = parts[2]  # Plain-language context
                    source_doc = parts[3]
                    clause_ref = parts[4]
                    conflict_type = parts[5]
                    rationale = parts[6]
                    
                    # Clean up vendor quote - remove surrounding quotes if present
                    vendor_quote_clean = vendor_quote.strip()
                    if vendor_quote_clean.startswith('"') and vendor_quote_clean.endswith('"'):
                        vendor_quote_clean = vendor_quote_clean[1:-1]
                    
                    # Create redline item using exact vendor quote for matching
                    if vendor_quote_clean.strip():  # Only add if we have actual text
                        redline_items.append({
                            'text': vendor_quote_clean.strip(),  # Exact sentence from vendor document
                            'comment': f"CONFLICT {clarification_id} ({conflict_type}): {rationale}",
                            'author': 'Legal-AI',
                            'initials': 'LAI',
                            'clarification_id': clarification_id,
                            'conflict_type': conflict_type,
                            'source_doc': source_doc,
                            'clause_ref': clause_ref,
                            'summary': summary
                        })
                        
        logger.info(f"PARSE_COMPLETE: Parsed {len(redline_items)} conflicts from analysis")
        for i, item in enumerate(redline_items[:2]):
            logger.info(f"PARSE_CONFLICT_{i+1}: ID={item.get('clarification_id')}, Text='{item.get('text', '')[:60]}...'")

        
    except Exception as e:
        logger.error(f"Error parsing conflicts for redlining: {str(e)}")
    
    return redline_items


def apply_exact_sentence_redlining(doc, redline_items: List[Dict[str, str]]) -> Dict[str, Any]:
    """
    Apply redlining to document by highlighting conflict text in red.
    Multi-tier search strategy for maximum conflict detection accuracy.
    
    Args:
        doc: python-docx Document object
        redline_items: List of conflict items with text to highlight
        
    Returns:
        Dictionary with redlining results
    """
    logger.info(f"APPLY_START: Processing {len(redline_items)} conflicts across {len(doc.paragraphs)} paragraphs")
    try:
        matches_found = 0
        paragraphs_with_redlines = []
        total_paragraphs = len(doc.paragraphs)
        failed_matches = []
        
        # Enhanced logging: Document structure analysis
        logger.info(f"DOCUMENT_STRUCTURE: Total paragraphs: {total_paragraphs}")
        logger.info(f"DOCUMENT_STRUCTURE: Total pages estimated: {total_paragraphs // 20}")  # Rough estimate
        logger.info(f"REDLINE_ITEMS: Processing {len(redline_items)} conflicts")
        

        
        # Track unmatched conflicts across tiers
        unmatched_conflicts = redline_items.copy()
        
        # TIER 0: Ultra-aggressive matching for difficult cases
        remaining_conflicts = []
        logger.info(f"APPLY_TIER0: Matches: {matches_found}, Remaining: {len(remaining_conflicts)}")
        
        for redline_item in unmatched_conflicts:
            vendor_conflict_text = redline_item.get('text', '').strip()
            if not vendor_conflict_text:
                continue
                
            # Enhanced logging: Track each conflict attempt
            conflict_id = redline_item.get('id', 'Unknown')
            source_doc = redline_item.get('source_doc', 'Unknown')
            logger.info(f"CONFLICT_ATTEMPT: ID={conflict_id}, Source={source_doc}, Text='{vendor_conflict_text[:100]}...'")
                
            found_match = _tier0_ultra_aggressive_matching(doc, vendor_conflict_text, redline_item)
            
            if found_match:
                matches_found += 1
                if found_match['para_idx'] not in paragraphs_with_redlines:
                    paragraphs_with_redlines.append(found_match['para_idx'])
                logger.info(f"CONFLICT_MATCHED: ID={conflict_id}, Paragraph={found_match['para_idx']}, Page≈{found_match['para_idx'] // 20}")
            else:
                remaining_conflicts.append(redline_item)
                logger.info(f"CONFLICT_NO_MATCH: ID={conflict_id}, Text='{vendor_conflict_text[:50]}...'")
        
        # Early exit if all conflicts matched
        if not remaining_conflicts:
            pass
        else:
            pass
            
            # TIER 1: Standard exact matching
            logger.info(f"APPLY_TIER1: Matches: {matches_found}, Remaining: {len(remaining_conflicts)}")
            
            unmatched_conflicts = remaining_conflicts
            remaining_conflicts = []
            
            for redline_item in unmatched_conflicts:
                vendor_conflict_text = redline_item.get('text', '').strip()
                if not vendor_conflict_text:
                    continue
                    
                # Enhanced logging: Track each conflict attempt
                conflict_id = redline_item.get('id', 'Unknown')
                source_doc = redline_item.get('source_doc', 'Unknown')
                logger.info(f"CONFLICT_ATTEMPT: ID={conflict_id}, Source={source_doc}, Text='{vendor_conflict_text[:100]}...'")
                    
                found_match = _tier1_exact_matching(doc, vendor_conflict_text, redline_item)
                
                if found_match:
                    matches_found += 1
                    if found_match['para_idx'] not in paragraphs_with_redlines:
                        paragraphs_with_redlines.append(found_match['para_idx'])
                    logger.info(f"CONFLICT_MATCHED: ID={conflict_id}, Paragraph={found_match['para_idx']}, Page≈{found_match['para_idx'] // 20}")
                else:
                    remaining_conflicts.append(redline_item)
                    logger.info(f"CONFLICT_NO_MATCH: ID={conflict_id}, Text='{vendor_conflict_text[:50]}...'")
        
        # Early exit if all conflicts matched
        if not remaining_conflicts:
            pass
        else:
            pass
            
            # TIER 2: Fuzzy matching (only for unmatched conflicts)
            logger.info(f"APPLY_TIER2: Matches: {matches_found}, Remaining: {len(remaining_conflicts)}")

            unmatched_conflicts = remaining_conflicts
            remaining_conflicts = []
            
            for redline_item in unmatched_conflicts:
                vendor_conflict_text = redline_item.get('text', '').strip()
                conflict_id = redline_item.get('id', 'Unknown')
                found_match = _tier2_fuzzy_matching(doc, vendor_conflict_text, redline_item)
                
                if found_match:
                    matches_found += 1
                    if found_match['para_idx'] not in paragraphs_with_redlines:
                        paragraphs_with_redlines.append(found_match['para_idx'])
                    logger.info(f"TIER2_MATCHED: ID={conflict_id}, Paragraph={found_match['para_idx']}, Page≈{found_match['para_idx'] // 20}")
                else:
                    remaining_conflicts.append(redline_item)
                    logger.info(f"TIER2_NO_MATCH: ID={conflict_id}, Text='{vendor_conflict_text[:50]}...'")
            
            # Early exit if all conflicts matched
            if not remaining_conflicts:
                pass
            else:
                pass
                
                # TIER 3: Cross-paragraph matching (only for unmatched conflicts)
                logger.info(f"APPLY_TIER3: Matches: {matches_found}, Remaining: {len(remaining_conflicts)}")
                unmatched_conflicts = remaining_conflicts
                remaining_conflicts = []
                
                for redline_item in unmatched_conflicts:
                    vendor_conflict_text = redline_item.get('text', '').strip()
                    found_match = _tier3_cross_paragraph_matching(doc, vendor_conflict_text, redline_item)
                    
                    if found_match:
                        matches_found += 1
                        for para_idx in found_match['para_indices']:
                            if para_idx not in paragraphs_with_redlines:
                                paragraphs_with_redlines.append(para_idx)

                    else:
                        remaining_conflicts.append(redline_item)
                
                # Early exit if all conflicts matched
                if not remaining_conflicts:
                    pass
                else:
                    pass
                    
                    # TIER 4: Partial phrase matching (only for unmatched conflicts)
                    logger.info(f"APPLY_TIER4: Matches: {matches_found}, Remaining: {len(remaining_conflicts)}")
                    unmatched_conflicts = remaining_conflicts
                    remaining_conflicts = []
                    
                    for redline_item in unmatched_conflicts:
                        vendor_conflict_text = redline_item.get('text', '').strip()
                        found_match = _tier4_partial_phrase_matching(doc, vendor_conflict_text, redline_item)
                        
                        if found_match:
                            matches_found += 1
                            if found_match['para_idx'] not in paragraphs_with_redlines:
                                paragraphs_with_redlines.append(found_match['para_idx'])

                        else:
                            remaining_conflicts.append(redline_item)
                    
                    # Early exit if all conflicts matched
                    if not remaining_conflicts:
                        pass
                    else:
                        pass
                        
                        # TIER 5: Tokenized matching (only for unmatched conflicts)
                        logger.info(f"APPLY_TIER5: Matches: {matches_found}, Remaining: {len(remaining_conflicts)}")

                        unmatched_conflicts = remaining_conflicts
                        remaining_conflicts = []
                        
                        for redline_item in unmatched_conflicts:
                            vendor_conflict_text = redline_item.get('text', '').strip()
                            found_match = _tier5_tokenized_matching(doc, vendor_conflict_text, redline_item)
                            
                            if found_match:
                                matches_found += 1
                                if found_match['para_idx'] not in paragraphs_with_redlines:
                                    paragraphs_with_redlines.append(found_match['para_idx'])

                            else:
                                remaining_conflicts.append(redline_item)
                        
                        # Final failed matches
                        logger.info(f"APPLY_FINAL: About to process {len(remaining_conflicts)} remaining conflicts as failed matches")
                        for redline_item in remaining_conflicts:
                            failed_matches.append({
                                'text': redline_item.get('text', ''),
                                'clarification_id': redline_item.get('clarification_id', 'Unknown')
                            })
        
        # Enhanced logging: Page distribution analysis
        if paragraphs_with_redlines:
            pages_affected = set(para_idx // 20 for para_idx in paragraphs_with_redlines)
            logger.info(f"PAGE_DISTRIBUTION: {len(pages_affected)} pages affected: {sorted(pages_affected)}")
            logger.info(f"PARAGRAPH_DISTRIBUTION: {len(paragraphs_with_redlines)} paragraphs redlined: {sorted(paragraphs_with_redlines)}")
        
        # Log final summary
        if failed_matches:
            logger.warning(f"REDLINING_FAILED: {len(failed_matches)} conflicts could not be matched")
            for failed in failed_matches:
                pass
        else:
            pass
        
        pass
        
        return {
            "total_paragraphs": total_paragraphs,
            "matches_found": matches_found,
            "paragraphs_with_redlines": paragraphs_with_redlines,
            "failed_matches": failed_matches,
            "pages_affected": len(set(para_idx // 20 for para_idx in paragraphs_with_redlines)) if paragraphs_with_redlines else 0
        }
        
    except Exception as e:
        logger.error(f"Error in enhanced redlining: {str(e)}")
        return {
            "total_paragraphs": 0,
            "matches_found": 0,
            "paragraphs_with_redlines": [],
            "error": str(e)
        }


def _tier0_ultra_aggressive_matching(doc, vendor_conflict_text: str, redline_item: Dict[str, str]) -> Dict[str, Any]:
    """TIER 0: Ultra-aggressive matching for the most difficult cases."""
    
    def ultra_normalize_text(text):
        """Ultra-aggressive text normalization."""
        if not text:
            return ""
        
        # Remove ALL punctuation and normalize everything
        normalized = re.sub(r'[^\w\s]', ' ', text)  # Replace all punctuation with spaces
        normalized = re.sub(r'\s+', ' ', normalized)  # Normalize all whitespace
        normalized = normalized.lower().strip()
        return normalized
    
    def extract_meaningful_words(text, min_length=3):
        """Extract meaningful words from text."""
        words = text.split()
        meaningful_words = []
        
        for word in words:
            if len(word) >= min_length and not word.lower() in ['the', 'and', 'for', 'are', 'but', 'not', 'you', 'all', 'can', 'had', 'her', 'was', 'one', 'our', 'out', 'day', 'get', 'has', 'him', 'his', 'how', 'its', 'may', 'new', 'now', 'old', 'see', 'two', 'way', 'who', 'boy', 'did', 'man', 'oil', 'sit', 'try', 'use', 'she', 'too', 'any', 'may', 'say', 'she', 'use']:
                meaningful_words.append(word)
        
        return meaningful_words
    
    def find_word_sequence_match(search_words, para_words, min_match_ratio=0.6):
        """Find if a significant portion of search words appear in sequence in paragraph."""
        if len(search_words) < 3:
            return False
        
        # Try to find consecutive word sequences
        for i in range(len(para_words) - len(search_words) + 1):
            sequence = para_words[i:i + len(search_words)]
            matches = sum(1 for j, word in enumerate(search_words) if word.lower() == sequence[j].lower())
            if matches / len(search_words) >= min_match_ratio:
                return True
        
        return False
    
    # Ultra-normalize the search text
    ultra_normalized_search = ultra_normalize_text(vendor_conflict_text)
    search_words = extract_meaningful_words(ultra_normalized_search)
    
    logger.info(f"TIER0_SEARCH: Ultra-normalized: '{ultra_normalized_search[:100]}...'")
    logger.info(f"TIER0_WORDS: Extracted {len(search_words)} meaningful words")
    
    # Search through all paragraphs
    for para_idx, paragraph in enumerate(doc.paragraphs):
        para_text = paragraph.text.strip()
        if not para_text or len(para_text) < 10:
            continue
        
        ultra_normalized_para = ultra_normalize_text(para_text)
        para_words = extract_meaningful_words(ultra_normalized_para)
        
        # Check if ultra-normalized search text is in ultra-normalized paragraph
        if ultra_normalized_search in ultra_normalized_para:
            logger.info(f"TIER0_MATCH: Found ultra-normalized match in paragraph {para_idx}")
            _apply_redline_to_paragraph(paragraph, vendor_conflict_text[:100], redline_item)
            return {'para_idx': para_idx, 'matched_text': 'ultra_normalized_match'}
        
        # Try word sequence matching
        if len(search_words) >= 3 and find_word_sequence_match(search_words, para_words):
            logger.info(f"TIER0_WORD_SEQUENCE: Found word sequence match in paragraph {para_idx}")
            _apply_redline_to_paragraph(paragraph, vendor_conflict_text[:100], redline_item)
            return {'para_idx': para_idx, 'matched_text': 'word_sequence_match'}
        
        # Try partial word matching (at least 70% of meaningful words match)
        if len(search_words) >= 5:
            word_matches = sum(1 for word in search_words if word.lower() in ultra_normalized_para)
            if word_matches / len(search_words) >= 0.7:
                logger.info(f"TIER0_WORD_PARTIAL: Found {word_matches}/{len(search_words)} word match in paragraph {para_idx}")
                _apply_redline_to_paragraph(paragraph, vendor_conflict_text[:100], redline_item)
                return {'para_idx': para_idx, 'matched_text': 'word_partial_match'}
    
    return None


def _tier1_exact_matching(doc, vendor_conflict_text: str, redline_item: Dict[str, str]) -> Dict[str, Any]:
    """TIER 1: Enhanced exact and case-insensitive matching within paragraphs."""
    
    def create_text_variations(text):
        """Create comprehensive text variations for matching."""
        variations = [
            text,  # Original text
            text.strip('"\''),  # Remove quotes
            text.replace('"', '"').replace('"', '"'),  # Smart quotes to regular
            text.replace(''', "'").replace(''', "'"),  # Smart apostrophes
            text.replace('"', '"').replace('"', '"'),  # Alternative quote normalization
            text.replace(''', "'").replace(''', "'"),  # Alternative apostrophe normalization
            # Additional variations for better matching
            re.sub(r'\s+', ' ', text.strip()),  # Normalize whitespace
            text.replace('\n', ' ').replace('\r', ' '),  # Remove line breaks
            text.replace('\t', ' '),  # Replace tabs with spaces
            re.sub(r'[^\w\s]', '', text),  # Remove punctuation
            text.replace('  ', ' '),  # Remove double spaces
            text.replace('   ', ' '),  # Remove triple spaces
            # More aggressive normalization
            re.sub(r'[^\w\s]', ' ', text).strip(),  # Replace punctuation with spaces
            re.sub(r'\s+', ' ', re.sub(r'[^\w\s]', ' ', text)).strip(),  # Combined normalization
        ]
        
        # Remove duplicates while preserving order
        return list(dict.fromkeys([v for v in variations if v.strip()]))
    
    # Create comprehensive variations of the text to try matching
    text_variations = create_text_variations(vendor_conflict_text)
    
    # Enhanced logging for debugging
    logger.info(f"TIER1_SEARCH: Looking for '{vendor_conflict_text[:50]}...' with {len(text_variations)} variations")
    
    # Search through all paragraphs for exact vendor conflict text
    for para_idx, paragraph in enumerate(doc.paragraphs):
        para_text = paragraph.text.strip()
        if not para_text:
            continue
            
        # Try each text variation for matching
        for i, text_variant in enumerate(text_variations):
            if not text_variant:
                continue
                
            # Try exact substring match (most reliable with exact vendor quotes)
            if text_variant in para_text:
                logger.info(f"TIER1_MATCH: Found exact match (variation {i}) in paragraph {para_idx}")
                _apply_redline_to_paragraph(paragraph, text_variant, redline_item)
                return {'para_idx': para_idx, 'matched_text': text_variant}
            
            # Try case-insensitive match as fallback
            elif text_variant.lower() in para_text.lower():
                logger.info(f"TIER1_CASE_MATCH: Found case-insensitive match (variation {i}) in paragraph {para_idx}")
                # Find the actual text with correct case in the document
                start_idx = para_text.lower().find(text_variant.lower())
                actual_text = para_text[start_idx:start_idx + len(text_variant)]
                _apply_redline_to_paragraph(paragraph, actual_text, redline_item)
                return {'para_idx': para_idx, 'matched_text': actual_text}
    
    return None


def _tier2_fuzzy_matching(doc, vendor_conflict_text: str, redline_item: Dict[str, str]) -> Dict[str, Any]:
    """TIER 2: Enhanced fuzzy matching with improved text normalization."""
    
    import difflib
    
    def enhanced_normalize_text(text):
        """Enhanced text normalization for better matching."""
        if not text:
            return ""
        
        # Remove all punctuation except spaces and normalize quotes/dashes
        normalized = re.sub(r'[""''`]', '"', text)  # Normalize quotes
        normalized = re.sub(r'[–—]', '-', normalized)  # Normalize dashes
        normalized = re.sub(r'[^\w\s]', ' ', normalized)  # Remove all punctuation except spaces
        normalized = re.sub(r'\s+', ' ', normalized)  # Normalize all whitespace to single spaces
        normalized = normalized.lower().strip()
        return normalized
    
    def similarity_ratio(a, b):
        """Calculate similarity ratio between two strings."""
        return difflib.SequenceMatcher(None, a, b).ratio()
    
    def extract_key_phrases(text, min_length=10):
        """Extract meaningful phrases from text for partial matching."""
        # Split on common separators
        phrases = re.split(r'[,.;:]|\sand\s|\sor\s|\sbut\s|\swith\s', text)
        key_phrases = []
        
        for phrase in phrases:
            phrase = phrase.strip()
            if len(phrase) >= min_length and not phrase.lower().startswith(('the ', 'a ', 'an ', 'to ', 'for ', 'of ', 'in ', 'on ', 'at ')):
                key_phrases.append(phrase)
        
        return key_phrases
    
    # Normalize the search text
    normalized_search = enhanced_normalize_text(vendor_conflict_text)
    
    # Enhanced logging for debugging
    logger.info(f"TIER2_SEARCH: Looking for normalized text: '{normalized_search[:100]}...'")
    
    # Try fuzzy matching with normalized text
    for para_idx, paragraph in enumerate(doc.paragraphs):
        para_text = paragraph.text.strip()
        if not para_text or len(para_text) < 10:  # Lowered threshold for short paragraphs
            continue
        
        normalized_para = enhanced_normalize_text(para_text)
        
        # Check if normalized search text is in normalized paragraph
        if normalized_search in normalized_para:
            logger.info(f"TIER2_MATCH: Found exact normalized match in paragraph {para_idx}")
            _apply_redline_to_paragraph(paragraph, vendor_conflict_text[:100], redline_item)
            return {'para_idx': para_idx, 'matched_text': 'normalized_match'}
        
        # Try similarity matching with lowered threshold
        if len(normalized_search) > 30:  # Lowered threshold for similarity matching
            similarity = similarity_ratio(normalized_search, normalized_para)
            if similarity > 0.75:  # Lowered threshold from 0.85 to 0.75
                logger.info(f"TIER2_SIMILARITY: Found similarity match (ratio: {similarity:.3f}) in paragraph {para_idx}")
                _apply_redline_to_paragraph(paragraph, vendor_conflict_text[:100], redline_item)
                return {'para_idx': para_idx, 'matched_text': 'similarity_match'}
        
        # Try partial phrase matching for longer texts
        if len(normalized_search) > 100:
            key_phrases = extract_key_phrases(normalized_search)
            for phrase in key_phrases:
                normalized_phrase = enhanced_normalize_text(phrase)
                if normalized_phrase in normalized_para and len(normalized_phrase) > 20:
                    logger.info(f"TIER2_PHRASE: Found phrase match '{phrase[:50]}...' in paragraph {para_idx}")
                    _apply_redline_to_paragraph(paragraph, phrase[:100], redline_item)
                    return {'para_idx': para_idx, 'matched_text': 'phrase_match'}
    
    return None


def _tier3_cross_paragraph_matching(doc, vendor_conflict_text: str, redline_item: Dict[str, str]) -> Dict[str, Any]:
    """TIER 3: Cross-paragraph matching for text that spans multiple paragraphs."""
    
    # Combine consecutive paragraphs and search across boundaries
    for start_idx in range(len(doc.paragraphs) - 1):
        # Try combining 2-5 consecutive paragraphs
        for span in range(2, min(6, len(doc.paragraphs) - start_idx + 1)):
            combined_text = ""
            para_indices = []
            
            for i in range(start_idx, start_idx + span):
                if doc.paragraphs[i].text.strip():
                    combined_text += doc.paragraphs[i].text.strip() + " "
                    para_indices.append(i)
            
            if not combined_text.strip():
                continue
            
            # Try exact match in combined text
            if vendor_conflict_text in combined_text:

                # Apply redlining to the first paragraph that contains part of the text
                _apply_redline_to_paragraph(doc.paragraphs[para_indices[0]], vendor_conflict_text[:100], redline_item)
                return {'para_indices': para_indices, 'matched_text': vendor_conflict_text}
            
            # Try case-insensitive match in combined text
            if vendor_conflict_text.lower() in combined_text.lower():

                _apply_redline_to_paragraph(doc.paragraphs[para_indices[0]], vendor_conflict_text[:100], redline_item)
                return {'para_indices': para_indices, 'matched_text': vendor_conflict_text}
    
    return None


def _tier4_partial_phrase_matching(doc, vendor_conflict_text: str, redline_item: Dict[str, str]) -> Dict[str, Any]:
    """TIER 4: Partial phrase matching for long sentences - match key phrases."""
    
    # Break long conflict text into key phrases (remove common words)
    def extract_key_phrases(text, min_length=15):
        """Extract meaningful phrases from text."""
        # Split on sentence boundaries and conjunctions
        phrases = re.split(r'[,.;:]|\sand\s|\sor\s|\sbut\s', text)
        key_phrases = []
        
        for phrase in phrases:
            phrase = phrase.strip()
            if len(phrase) >= min_length and not phrase.lower().startswith(('the ', 'a ', 'an ', 'to ', 'for ')):
                key_phrases.append(phrase)
        
        return key_phrases
    
    # Only try partial matching for longer conflict texts
    if len(vendor_conflict_text) < 80:
        return None
    
    key_phrases = extract_key_phrases(vendor_conflict_text)
    
    for para_idx, paragraph in enumerate(doc.paragraphs):
        para_text = paragraph.text.strip()
        if not para_text or len(para_text) < 30:
            continue
        
        # Check if any key phrase matches
        for phrase in key_phrases:
            if len(phrase) > 20 and phrase.lower() in para_text.lower():

                # Apply redlining to the found phrase instead of full text
                start_idx = para_text.lower().find(phrase.lower())
                actual_phrase = para_text[start_idx:start_idx + len(phrase)]
                _apply_redline_to_paragraph(paragraph, actual_phrase, redline_item)
                return {'para_idx': para_idx, 'matched_text': actual_phrase}
    
    return None


def _tier5_tokenized_matching(doc, vendor_conflict_text: str, redline_item: Dict[str, str]) -> Dict[str, Any]:
    """TIER 5: Tokenized matching to handle formatting differences and word boundary issues."""
    
    def tokenize_for_matching(text):
        """Tokenize text for flexible matching."""
        # Remove punctuation except periods in numbers
        cleaned = re.sub(r'[^\w\s\.]', ' ', text)
        # Split into tokens and remove empty strings
        tokens = [token.lower().strip() for token in cleaned.split() if token.strip()]
        return tokens
    
    def token_sequence_match(search_tokens, text_tokens, min_match_ratio=0.7):
        """Find token sequence with minimum match ratio."""
        search_len = len(search_tokens)
        text_len = len(text_tokens)
        
        if search_len == 0 or text_len == 0:
            return False, -1
        
        for start_idx in range(text_len - search_len + 1):
            matched_tokens = 0
            for i in range(search_len):
                if search_tokens[i] == text_tokens[start_idx + i]:
                    matched_tokens += 1
            
            match_ratio = matched_tokens / search_len
            if match_ratio >= min_match_ratio:
                return True, start_idx
        
        return False, -1
    
    # Tokenize the search text
    search_tokens = tokenize_for_matching(vendor_conflict_text)
    
    # Only try tokenized matching for substantial texts
    if len(search_tokens) < 5:
        return None
    
    for para_idx, paragraph in enumerate(doc.paragraphs):
        para_text = paragraph.text.strip()
        if not para_text:
            continue
        
        para_tokens = tokenize_for_matching(para_text)
        
        # Try token sequence matching
        found, start_pos = token_sequence_match(search_tokens, para_tokens)
        
        if found:

            # Reconstruct approximate text for redlining (use first part of vendor text)
            redline_text = vendor_conflict_text[:min(len(vendor_conflict_text), 150)]
            _apply_redline_to_paragraph(paragraph, redline_text, redline_item)
            return {'para_idx': para_idx, 'matched_text': 'tokenized_match'}
    
    return None


def _apply_redline_to_paragraph(paragraph, conflict_text: str, redline_item: Dict[str, str]):
    """Apply redline formatting to specific text within a paragraph."""
    
    try:
        # Clear existing runs and rebuild with redlined text
        paragraph_text = paragraph.text
        
        # Find the position of conflict text
        start_pos = paragraph_text.find(conflict_text)
        if start_pos == -1:
            return
            
        # Clear all runs
        for run in paragraph.runs:
            run.clear()
        
        # Add text before conflict (normal formatting)
        if start_pos > 0:
            before_text = paragraph_text[:start_pos]
            run = paragraph.add_run(before_text)
        
        # Add conflict text with red strikethrough formatting (redlined)
        conflict_run = paragraph.add_run(conflict_text)
        conflict_run.font.color.rgb = RGBColor(255, 0, 0)  # Red color
        conflict_run.font.strike = True  # Strikethrough for redlining
        
        # Add comment to the specific conflict text run (not entire paragraph)
        comment = redline_item.get('comment', '')
        if comment:
            author = "One L"
            initials = "1L"
            conflict_run.add_comment(comment, author=author, initials=initials)
        
        # Add text after conflict (normal formatting)
        end_pos = start_pos + len(conflict_text)
        if end_pos < len(paragraph_text):
            after_text = paragraph_text[end_pos:]
            run = paragraph.add_run(after_text)
            

        
    except Exception as e:
        logger.error(f"Error applying redline to paragraph: {str(e)}")


def _get_bucket_name(bucket_type: str) -> str:
    """Get the appropriate bucket name based on type."""
    if bucket_type == "knowledge":
        return os.environ.get("KNOWLEDGE_BUCKET")
    elif bucket_type == "user_documents":
        return os.environ.get("USER_DOCUMENTS_BUCKET")
    elif bucket_type == "agent_processing":
        return os.environ.get("AGENT_PROCESSING_BUCKET")
    else:
        raise ValueError(f"Invalid bucket_type: {bucket_type}")


def _copy_document_to_processing(original_s3_key: str, source_bucket: str, agent_bucket: str) -> str:
    """Copy document to agent processing bucket with organized structure."""
    
    from datetime import datetime
    import uuid
    
    try:
        # Create organized folder structure
        timestamp = datetime.utcnow().strftime("%Y/%m/%d")
        unique_id = str(uuid.uuid4())[:8]
        
        # Extract filename
        filename = original_s3_key.split('/')[-1]
        
        # Create new key with organized structure
        agent_key = f"input/{timestamp}/{unique_id}_{filename}"
        
        # Copy object to agent processing bucket
        s3_client.copy_object(
            CopySource={'Bucket': source_bucket, 'Key': original_s3_key},
            Bucket=agent_bucket,
            Key=agent_key,
            MetadataDirective='COPY'
        )
        

        return agent_key
        
    except Exception as e:
        logger.error(f"Error copying document to processing bucket: {str(e)}")
        raise


def _download_and_load_document(bucket: str, s3_key: str):
    """
    Download document from S3 and load it using python-docx.
    
    Args:
        bucket: S3 bucket name
        s3_key: S3 key of the document
        
    Returns:
        python-docx Document object
    """
    
    try:

        
        # Download the document from S3
        response = s3_client.get_object(Bucket=bucket, Key=s3_key)
        document_content = response['Body'].read()
        
        # Load the document using python-docx
        doc = Document(io.BytesIO(document_content))
        

        return doc
        
    except Exception as e:
        logger.error(f"Error downloading and loading document: {str(e)}")
        raise Exception(f"Failed to download and load document: {str(e)}")


def _create_redlined_filename(original_s3_key: str, session_id: str = None, user_id: str = None) -> str:
    """
    Create a filename for the redlined document.
    
    Args:
        original_s3_key: Original document S3 key
        session_id: Session ID for organizing output files
        user_id: User ID for organizing output files
        
    Returns:
        New S3 key for redlined document
    """
    
    # Split the path and filename
    path_parts = original_s3_key.split('/')
    filename = path_parts[-1]
    
    # Add redlined suffix
    name_parts = filename.rsplit('.', 1)
    if len(name_parts) == 2:
        redlined_filename = f"{name_parts[0]}_REDLINED.{name_parts[1]}"
    else:
        redlined_filename = f"{filename}_REDLINED"
    
    # Create session-based path if session info is provided
    if session_id and user_id:
        redlined_s3_key = f"sessions/{user_id}/{session_id}/output/{redlined_filename}"
    else:
        # Fallback to original logic for backwards compatibility
        if len(path_parts) > 1:
            # Change 'input' to 'output' for redlined documents
            new_path_parts = [part if part != 'input' else 'output' for part in path_parts[:-1]]
            redlined_s3_key = '/'.join(new_path_parts) + '/' + redlined_filename
        else:
            redlined_s3_key = f"output/{redlined_filename}"
    
    return redlined_s3_key


def save_analysis_to_dynamodb(
    analysis_id: str,
    document_s3_key: str,
    analysis_data: str,
    bucket_type: str,
    usage_data: Dict[str, Any],
    thinking: str = "",
    citations: List[Dict[str, Any]] = None,
    session_id: str = None,
    user_id: str = None
) -> Dict[str, Any]:
    """
    Save analysis results to DynamoDB table including parsed conflicts.
    
    Args:
        analysis_id: Unique identifier for this analysis
        document_s3_key: S3 key of the analyzed document
        analysis_data: The analysis text containing conflicts table
        bucket_type: Source bucket type
        usage_data: Model usage statistics
        thinking: AI thinking process
        citations: Knowledge base citations
        
    Returns:
        Dictionary indicating success/failure of save operation
    """
    
    try:
        table_name = os.environ.get('ANALYSIS_TABLE')
        if not table_name:
            return {
                "success": False,
                "error": "ANALYSIS_TABLE environment variable not set"
            }
        
        import boto3
        from datetime import datetime
        dynamodb_resource = boto3.resource('dynamodb')
        table = dynamodb_resource.Table(table_name)
        timestamp = datetime.utcnow().isoformat()
        
        # Parse conflicts from analysis data for structured storage
        redline_items = parse_conflicts_for_redlining(analysis_data)
        
        # Convert redline format to DynamoDB format with better naming
        conflicts = []
        for item in redline_items:
            conflicts.append({
                'clarification_id': item['clarification_id'],
                'vendor_conflict': item['text'],  # Exact text from vendor document (better naming)
                'source_doc': item['source_doc'],
                'clause_ref': item['clause_ref'],
                'conflict_type': item['conflict_type'],
                'rationale': item['comment'].split('): ', 1)[-1] if '): ' in item['comment'] else item['comment']
            })
        
        # Prepare streamlined item for DynamoDB - focusing only on conflicts data
        item = {
            'analysis_id': analysis_id,
            'timestamp': timestamp,
            'document_s3_key': document_s3_key,
            'conflicts_count': len(conflicts),
            'conflicts': conflicts
        }
        
        # Add session and user linking if provided
        if session_id:
            item['session_id'] = session_id
        if user_id:
            item['user_id'] = user_id
        
        # Save to DynamoDB
        table.put_item(Item=item)
        

        
        return {
            "success": True,
            "analysis_id": analysis_id,
            "conflicts_saved": len(conflicts)
        }
        
    except Exception as e:
        logger.error(f"Error saving analysis to DynamoDB: {str(e)}")
        return {
            "success": False,
            "error": str(e)
        }


def _save_and_upload_document(doc, bucket: str, s3_key: str, metadata: Dict[str, str]) -> bool:
    """
    Save python-docx document to memory and upload to S3.
    
    Args:
        doc: python-docx Document object
        bucket: S3 bucket name
        s3_key: S3 key for the uploaded document
        metadata: Dictionary of metadata to attach to S3 object
        
    Returns:
        Boolean indicating success
    """
    
    try:

        
        # Save document to memory buffer
        buffer = io.BytesIO()
        doc.save(buffer)
        buffer.seek(0)
        
        # Upload to S3 with metadata
        s3_client.put_object(
            Bucket=bucket,
            Key=s3_key,
            Body=buffer.getvalue(),
            ContentType='application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            Metadata=metadata
        )
        

        return True
        
    except Exception as e:
        logger.error(f"Error saving and uploading document: {str(e)}")
        return False


def _cleanup_session_documents(session_id: str, user_id: str) -> Dict[str, Any]:
    """
    Clean up session reference documents using existing Lambda functions.
    
    Steps:
    1. List session reference documents
    2. Call delete_from_s3 Lambda function
    3. Call sync_knowledge_base Lambda function
    """
    try:

        
        # Step 1: List session reference documents
        session_s3_keys = _list_session_reference_documents(session_id, user_id)
        
        if not session_s3_keys:
            logger.info(f"No reference documents found for cleanup in session {session_id}")
            return {
                'success': True,
                'deleted_count': 0,
                'sync_triggered': False,
                'message': 'No documents to clean up'
            }
        
        logger.info(f"Found {len(session_s3_keys)} reference documents to delete")
        
        # Step 2: Delete documents using existing delete_from_s3 Lambda
        delete_result = _invoke_delete_lambda(session_s3_keys)
        
        if not delete_result.get('success'):
            return {
                'success': False,
                'error': f"Document deletion failed: {delete_result.get('error')}",
                'step_failed': 'delete',
                'found_documents': len(session_s3_keys)
            }
        
        deleted_count = delete_result.get('deleted_count', 0)
        logger.info(f"Successfully deleted {deleted_count} reference documents")
        
        # Step 3: Trigger knowledge base sync using existing sync Lambda
        sync_result = _invoke_sync_lambda()
        
        return {
            'success': True,
            'deleted_count': deleted_count,
            'sync_triggered': sync_result.get('success', False),
            'sync_job_id': sync_result.get('job_id'),
            'message': f"Cleanup completed: {deleted_count} documents deleted, sync triggered"
        }
        
    except Exception as e:
        logger.error(f"Session cleanup failed: {str(e)}")
        return {
            'success': False,
            'error': str(e)
        }


def _get_function_names() -> Dict[str, str]:
    """Get Lambda function names based on current function naming pattern."""
    current_function = os.environ.get('AWS_LAMBDA_FUNCTION_NAME', '')
    
    if current_function and 'document-review' in current_function:
        # Extract stack name: OneLStack-document-review -> OneLStack
        stack_name = current_function.replace('-document-review', '')
        
        return {
            'delete_function': f"{stack_name}-delete-from-s3",
            'sync_function': f"{stack_name}-sync-knowledge-base"
        }
    else:
        # Fallback: use known stack name
        return {
            'delete_function': 'OneLStack-delete-from-s3',
            'sync_function': 'OneLStack-sync-knowledge-base'
        }


def _list_session_reference_documents(session_id: str, user_id: str) -> List[str]:
    """List all reference document S3 keys for a session."""
    try:
        bucket_name = os.environ.get('USER_DOCUMENTS_BUCKET')
        if not bucket_name:
            logger.error('USER_DOCUMENTS_BUCKET not configured')
            return []
        
        # Session reference docs are at: sessions/{user_id}/{session_id}/reference-docs/
        prefix = f"sessions/{user_id}/{session_id}/reference-docs/"
        
        logger.info(f"Listing documents with prefix: {prefix}")
        
        # List all objects with this prefix
        response = s3_client.list_objects_v2(Bucket=bucket_name, Prefix=prefix)
        
        if 'Contents' not in response:
            return []
        
        # Extract S3 keys
        s3_keys = [obj['Key'] for obj in response['Contents']]
        
        logger.info(f"Found {len(s3_keys)} reference documents: {s3_keys}")
        return s3_keys
        
    except Exception as e:
        logger.error(f"Error listing session documents: {str(e)}")
        return []


def _invoke_delete_lambda(s3_keys: List[str]) -> Dict[str, Any]:
    """Invoke the existing delete_from_s3 Lambda function."""
    try:
        lambda_client = boto3.client('lambda')
        
        function_names = _get_function_names()
        delete_function_name = function_names['delete_function']
        
        # Prepare payload for delete Lambda
        delete_payload = {
            'bucket_type': 'user_documents',
            's3_keys': s3_keys
        }
        
        logger.info(f"Invoking delete Lambda: {delete_function_name}")
        
        # Invoke delete Lambda synchronously to ensure completion
        response = lambda_client.invoke(
            FunctionName=delete_function_name,
            InvocationType='RequestResponse',  # Synchronous
            Payload=json.dumps(delete_payload)
        )
        
        # Parse response
        response_payload = json.loads(response['Payload'].read())
        
        if response_payload.get('statusCode') == 200:
            body = json.loads(response_payload.get('body', '{}'))
            logger.info(f"Delete Lambda succeeded: {body.get('message')}")
            return {
                'success': True,
                'deleted_count': body.get('deleted_count', 0),
                'details': body
            }
        else:
            error_msg = response_payload.get('body', 'Unknown error')
            logger.error(f"Delete Lambda failed: {error_msg}")
            return {
                'success': False,
                'error': error_msg
            }
            
    except Exception as e:
        logger.error(f"Error invoking delete Lambda: {str(e)}")
        return {
            'success': False,
            'error': str(e)
        }


def _invoke_sync_lambda() -> Dict[str, Any]:
    """Invoke the existing sync_knowledge_base Lambda function."""
    try:
        lambda_client = boto3.client('lambda')
        
        function_names = _get_function_names()
        sync_function_name = function_names['sync_function']
        
        # Prepare payload for sync Lambda
        sync_payload = {
            'action': 'start_sync',
            'data_source': 'user_documents',
            'triggered_by': 'session_cleanup'
        }
        
        logger.info(f"Invoking sync Lambda: {sync_function_name}")
        
        # Invoke sync Lambda asynchronously (sync can take time)
        response = lambda_client.invoke(
            FunctionName=sync_function_name,
            InvocationType='Event',  # Asynchronous
            Payload=json.dumps(sync_payload)
        )
        
        logger.info(f"Sync Lambda invoked successfully")
        
        return {
            'success': True,
            'function_invoked': sync_function_name,
            'message': 'Knowledge base sync triggered'
        }
        
    except Exception as e:
        logger.error(f"Error invoking sync Lambda: {str(e)}")
        return {
            'success': False,
            'error': str(e)
        }


 