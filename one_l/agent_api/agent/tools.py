"""
Tool functions for document review agent.
Provides knowledge base retrieval and document red-lining capabilities.
"""

import json
import boto3
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

# Initialize AWS clients
bedrock_agent_client = boto3.client('bedrock-agent-runtime')
s3_client = boto3.client('s3')

# Knowledge base optimization constants
MAX_CHUNK_SIZE = 2000  # Target tokens per chunk for optimal processing
MIN_RELEVANCE_SCORE = 0.7  # Minimum relevance score to include results
OPTIMAL_RESULTS_PER_QUERY = 30  # Optimized results per query (reduced from 100)
DEDUPLICATION_THRESHOLD = 0.85  # Similarity threshold for content deduplication

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
                "description": "Intelligently retrieve and optimize relevant documents from the knowledge base. Features automatic deduplication, relevance filtering, smart chunking, and throttling resilience. Use as many queries as needed for comprehensive coverage.",
                "inputSchema": {
                    "json": {
                        "type": "object",
                        "properties": {
                            "query": {
                                "type": "string",
                                "description": "Search query to find relevant reference documents. Use specific terms related to vendor clauses."
                            },
                            "max_results": {
                                "type": "integer",
                                "description": "Maximum number of results to retrieve (auto-optimized for performance, default: 30)",
                                "default": 30
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
    max_results: int = 30,
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
            logger.info(f"Retrieving from knowledge base {knowledge_base_id} with query: {query} (attempt {retry_count + 1})")
            
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
                    logger.warning(f"Throttling detected, retrying in {delay} seconds (attempt {retry_count + 1}/{MAX_RETRIES})")
                    time.sleep(delay)
                    return _retrieve_with_retry(retry_count + 1)
                else:
                    logger.error(f"Max retries exceeded for throttling on query: {query}")
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
            logger.info(f"Returning cached results for query: {query}")
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
        for result in response.get('retrievalResults', []):
            content = result.get('content', {})
            metadata = result.get('metadata', {})
            
            raw_results.append({
                "text": content.get('text', ''),
                "score": result.get('score', 0),
                "source": metadata.get('source', 'Unknown'),
                "metadata": metadata
            })
        
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
        
        logger.info(f"Successfully retrieved and optimized {len(optimized_results)} results for query: {query}")
        logger.info(f"Optimization: {len(raw_results)} raw → {len(optimized_results)} optimized ({duplicates_filtered} duplicates filtered, {chunks_created} chunks created)")
        
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
    logger.info("Knowledge base cache cleared for new session")

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
        # Get bucket configurations
        source_bucket = _get_bucket_name(bucket_type)
        agent_processing_bucket = os.environ.get('AGENT_PROCESSING_BUCKET')
        
        if not agent_processing_bucket or not source_bucket:
            return {
                "success": False,
                "error": "Required buckets not configured"
            }
        
        logger.info(f"Starting redlining workflow for document: {document_s3_key}")
        
        # Step 1: Copy document to agent processing bucket
        agent_document_key = _copy_document_to_processing(document_s3_key, source_bucket, agent_processing_bucket)
        
        # Step 2: Download and load the document
        doc = _download_and_load_document(agent_processing_bucket, agent_document_key)
        
        # Step 3: Parse conflicts and create redline items from analysis data
        redline_items = parse_conflicts_for_redlining(analysis_data)
        
        if not redline_items:
            logger.warning("No conflicts found for redlining")
            return {
                "success": False,
                "error": "No conflicts found in analysis data for redlining"
            }
        
        # Step 4: Apply redlining with exact sentence matching
        results = apply_exact_sentence_redlining(doc, redline_items)
        
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
        
        logger.info(f"Redlining completed successfully: {redlined_s3_key}")
        logger.info(f"Redlined document stored in bucket: {agent_processing_bucket}")
        logger.info(f"Full redlined document path: s3://{agent_processing_bucket}/{redlined_s3_key}")
        
        return {
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
        
        logger.info(f"Parsed {len(redline_items)} redline items from analysis data")
        
    except Exception as e:
        logger.error(f"Error parsing conflicts for redlining: {str(e)}")
    
    return redline_items


def apply_exact_sentence_redlining(doc, redline_items: List[Dict[str, str]]) -> Dict[str, Any]:
    """
    Apply redlining to document by highlighting conflict text in red.
    
    Args:
        doc: python-docx Document object
        redline_items: List of conflict items with text to highlight
        
    Returns:
        Dictionary with redlining results
    """
    
    try:
        matches_found = 0
        paragraphs_with_redlines = []
        total_paragraphs = len(doc.paragraphs)
        
        logger.info(f"Starting redlining process for {len(redline_items)} conflicts across {total_paragraphs} paragraphs")
        
        for redline_item in redline_items:
            vendor_conflict_text = redline_item.get('text', '').strip()
            if not vendor_conflict_text:
                continue
                
            logger.info(f"Looking for vendor conflict text: '{vendor_conflict_text}'")
            
            # Create variations of the text to try matching (handle various quote formats)
            text_variations = [
                vendor_conflict_text,  # Original text
                vendor_conflict_text.strip('"\''),  # Remove quotes
                vendor_conflict_text.replace('"', '"').replace('"', '"'),  # Smart quotes to regular
                vendor_conflict_text.replace(''', "'").replace(''', "'"),  # Smart apostrophes
            ]
            
            # Remove duplicates while preserving order
            text_variations = list(dict.fromkeys(text_variations))
            
            # Search through all paragraphs for exact vendor conflict text
            found_match = False
            for para_idx, paragraph in enumerate(doc.paragraphs):
                para_text = paragraph.text.strip()
                if not para_text:
                    continue
                    
                # Try each text variation for matching
                for text_variant in text_variations:
                    if not text_variant:
                        continue
                        
                    # Try exact substring match (most reliable with exact vendor quotes)
                    if text_variant in para_text:
                        logger.info(f"Found EXACT match in paragraph {para_idx}")
                        logger.info(f"Matched text variant: '{text_variant}'")
                        _apply_redline_to_paragraph(paragraph, text_variant, redline_item)
                        matches_found += 1
                        if para_idx not in paragraphs_with_redlines:
                            paragraphs_with_redlines.append(para_idx)
                        found_match = True
                        break
                    
                    # Try case-insensitive match as fallback
                    elif text_variant.lower() in para_text.lower():
                        logger.info(f"Found CASE-INSENSITIVE match in paragraph {para_idx}")
                        # Find the actual text with correct case in the document
                        start_idx = para_text.lower().find(text_variant.lower())
                        actual_text = para_text[start_idx:start_idx + len(text_variant)]
                        logger.info(f"Matched text variant: '{actual_text}'")
                        _apply_redline_to_paragraph(paragraph, actual_text, redline_item)
                        matches_found += 1
                        if para_idx not in paragraphs_with_redlines:
                            paragraphs_with_redlines.append(para_idx)
                        found_match = True
                        break
                
                if found_match:
                    break
            
            if not found_match:
                logger.warning(f"NO MATCH found for vendor conflict: '{vendor_conflict_text}'")
                # Sample a few paragraphs to help debug
                logger.debug("Sample document paragraphs:")
                for i, para in enumerate(doc.paragraphs[:3]):
                    if para.text.strip():
                        logger.debug(f"  Paragraph {i}: '{para.text[:100]}...'")
                        if i >= 2:  # Limit to 3 samples
                            break
        
        logger.info(f"Redlining complete: {matches_found} matches found in {len(paragraphs_with_redlines)} paragraphs")
        
        return {
            "total_paragraphs": total_paragraphs,
            "matches_found": matches_found,
            "paragraphs_with_redlines": paragraphs_with_redlines
        }
        
    except Exception as e:
        logger.error(f"Error in apply_exact_sentence_redlining: {str(e)}")
        return {
            "total_paragraphs": 0,
            "matches_found": 0,
            "paragraphs_with_redlines": [],
            "error": str(e)
        }


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
            
        logger.debug(f"Applied redlining to specific conflict text: {conflict_text[:30]}...")
        
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
        
        logger.info(f"Document copied to agent processing bucket: {agent_key}")
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
        logger.info(f"Downloading document from s3://{bucket}/{s3_key}")
        
        # Download the document from S3
        response = s3_client.get_object(Bucket=bucket, Key=s3_key)
        document_content = response['Body'].read()
        
        # Load the document using python-docx
        doc = Document(io.BytesIO(document_content))
        
        logger.info(f"Successfully loaded document with {len(doc.paragraphs)} paragraphs")
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
        
        logger.info(f"Successfully saved analysis {analysis_id} to DynamoDB with {len(conflicts)} conflicts")
        
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
        logger.info(f"Saving and uploading redlined document to s3://{bucket}/{s3_key}")
        
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
        
        logger.info(f"Successfully uploaded redlined document to S3: {s3_key}")
        return True
        
    except Exception as e:
        logger.error(f"Error saving and uploading document: {str(e)}")
        return False


 