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
import unicodedata
from typing import Dict, Any, List, Optional
from collections import defaultdict
from docx import Document
from docx.shared import RGBColor, Pt, Inches
import io

# Pydantic models for output validation
try:
    from pydantic import BaseModel, Field, field_validator, ConfigDict
    PYDANTIC_AVAILABLE = True
except ImportError:
    PYDANTIC_AVAILABLE = False
    BaseModel = None
    Field = None
    field_validator = None
    ConfigDict = None

# Import PDF processor
try:
    from .pdf_processor import PDFProcessor, is_pdf_file, get_pdf_processor
    PDF_SUPPORT_ENABLED = True
except ImportError:
    PDF_SUPPORT_ENABLED = False
    PDFProcessor = None
    def is_pdf_file(x): return False
    def get_pdf_processor(): return None

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Pydantic model for conflict validation
if PYDANTIC_AVAILABLE:
    class ConflictModel(BaseModel):
        """Pydantic model for validating conflict data from LLM output."""
        clarification_id: str = Field(..., description="Vendor's ID or Additional-[#] for other findings")
        vendor_quote: str = Field(..., description="Exact text verbatim OR 'N/A - Missing provision' for omissions")
        summary: str = Field(..., description="20-40 word context")
        source_doc: str = Field(..., description="Name of the actual Massachusetts source document retrieved from the knowledge base, OR 'N/A – General risk language not tied to a specific Massachusetts clause'")
        clause_ref: str = Field(default="N/A", description="Specific section or 'N/A' if not applicable")
        conflict_type: str = Field(..., description="adds/deletes/modifies/contradicts/omits required/reverses obligation")
        rationale: str = Field(..., description="≤50 words on legal impact")
        
        @field_validator('source_doc')
        @classmethod
        def validate_source_doc(cls, v: str) -> str:
            """Normalize source_doc but allow any value - no hard restrictions on conflict removal."""
            v_str = str(v).strip()
            # Return as-is, no validation/rejection - allow all conflicts through
            return v_str if v_str else ""
        
        @field_validator('vendor_quote')
        @classmethod
        def validate_vendor_quote(cls, v: str) -> str:
            """Clean up vendor quote by removing surrounding quotes."""
            v_str = str(v).strip()
            if v_str.startswith('"') and v_str.endswith('"'):
                v_str = v_str[1:-1]
            if not v_str or v_str.lower() in ['n/a', 'na', 'none', 'n.a.', 'n.a', 'not available'] or len(v_str) < 5:
                raise ValueError(f"vendor_quote must be meaningful text (at least 5 chars), got: '{v_str}'")
            return v_str
        
        model_config = ConfigDict(
            extra='forbid',  # Reject extra fields
            str_strip_whitespace=True  # Auto-strip strings
        )
    
    class RedliningResponseModel(BaseModel):
        """Pydantic model for validating the redlining response structure with explanation and conflicts."""
        explanation: str = Field(..., description="Justification/explanation in the form of text so the model can give more context")
        conflicts: List[ConflictModel] = Field(default_factory=list, description="Array of conflicts")
        
        model_config = ConfigDict(
            extra='forbid',  # Reject extra fields
            str_strip_whitespace=True  # Auto-strip strings
        )

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
    # Create a list of keys to iterate over to avoid "dictionary changed size during iteration" error
    # when multiple threads access the cache concurrently
    existing_signatures = list(_content_cache.keys())
    for existing_sig in existing_signatures:
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
                "description": """
Exhaustively retrieve ALL relevant reference documents for conflict detection.

**When to use:**
- After analyzing vendor document structure
- When you need to check vendor language against Massachusetts requirements
- For each distinct section or topic area in vendor document

**Query Construction Best Practices:**
- Include specific contract terms from vendor document
- Add legal phrases and Massachusetts-specific terminology
- Include synonyms and variations of key terms
- Target 50-100+ unique terms per query
- Make queries distinct and non-overlapping

**Examples of Effective Queries:**
- "liability indemnity insurance Massachusetts ITS Terms and Conditions unlimited coverage"
- "payment terms invoicing net 30 days Massachusetts Commonwealth requirements"
- "data ownership intellectual property confidentiality Massachusetts state contracts"

**Output:**
Returns array of relevant document chunks with text, metadata, and relevance scores.
Optimized for maximum coverage with deduplication and relevance filtering.

Use 6-12+ targeted queries to ensure no conflicts are missed.
""",
                "inputSchema": {
                    "json": {
                        "type": "object",
                        "properties": {
                            "query": {
                                "type": "string",
                                "description": "Targeted search query to find reference documents. Use specific contract terms, legal phrases, or vendor-specific language. Try variations of important terms to catch all relevant content. Target 50-100+ unique terms per query."
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

def _extract_source_from_result(metadata: Dict[str, Any], location: Dict[str, Any]) -> str:
    """
    Extract source document name from retrieval result metadata and location.
    
    Checks multiple possible fields where AWS Bedrock Knowledge Base might store
    the source information, in order of preference:
    1. metadata.source - primary source field
    2. location.s3Location.uri - full S3 URI
    3. location.s3Location.key - S3 key
    4. metadata.s3_location - alternative metadata field
    5. metadata.uri - URI in metadata
    6. Extract filename from S3 key if available
    
    Args:
        metadata: Metadata dictionary from retrieval result
        location: Location dictionary from retrieval result
        
    Returns:
        Source document name or descriptive fallback if not found
    """
    import os
    
    # Try metadata.source first (primary field)
    if metadata.get('source'):
        return metadata['source']
    
    # Try location.s3Location.uri (full S3 URI)
    s3_location = location.get('s3Location', {})
    if s3_location.get('uri'):
        uri = s3_location['uri']
        # Extract filename from URI (e.g., s3://bucket/path/file.pdf -> file.pdf)
        if '/' in uri:
            filename = uri.split('/')[-1]
            if filename:
                return filename
    
    # Try location.s3Location.key (S3 key)
    if s3_location.get('key'):
        s3_key = s3_location['key']
        # Extract filename from S3 key (e.g., path/to/file.pdf -> file.pdf)
        if '/' in s3_key:
            filename = s3_key.split('/')[-1]
            if filename:
                return filename
        return s3_key
    
    # Try metadata.s3_location (alternative metadata field)
    if metadata.get('s3_location'):
        s3_loc = metadata['s3_location']
        if '/' in s3_loc:
            filename = s3_loc.split('/')[-1]
            if filename:
                return filename
        return s3_loc
    
    # Try metadata.uri
    if metadata.get('uri'):
        uri = metadata['uri']
        if '/' in uri:
            filename = uri.split('/')[-1]
            if filename:
                return filename
        return uri
    
    # Try extracting from any metadata field that looks like a path or filename
    for key, value in metadata.items():
        if isinstance(value, str) and ('/' in value or '.' in value):
            # Check if it looks like a file path
            if any(ext in value.lower() for ext in ['.pdf', '.docx', '.doc', '.txt', '.html']):
                filename = value.split('/')[-1] if '/' in value else value
                if filename and len(filename) > 3:  # Reasonable filename length
                    return filename
    
    # If we have location info but no specific source, return a descriptive message
    if s3_location:
        bucket = s3_location.get('bucket', 'unknown-bucket')
        key = s3_location.get('key', 'unknown-key')
        # Extract filename from key if possible
        if '/' in key:
            filename = key.split('/')[-1]
            if filename:
                return filename
        return key if key != 'unknown-key' else f"{bucket}/{key}"
    
    # Last resort: log warning and return descriptive fallback
    logger.warning(f"Could not extract source from metadata: {metadata}, location: {location}")
    return 'Unknown Source'

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
        # Include knowledge_base_id in cache key to prevent cross-KB cache pollution
        cache_key = f"{knowledge_base_id}:{_calculate_content_signature(query)}"
        if cache_key in _query_cache:

            cached_result = _query_cache[cache_key].copy()
            cached_result["cached"] = True
            logger.info(f"QUERY_CACHE_HIT: Using cached results for KB={knowledge_base_id}, query_hash={cache_key.split(':')[1]}")
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
            location = result.get('location', {})
            
            # Extract source from multiple possible locations
            source = _extract_source_from_result(metadata, location)
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
                "query_hash": cache_key.split(':')[1],  # Extract just the query hash part
                "cache_key": cache_key,
                "processing_successful": True,
                "optimization_ratio": f"{len(optimized_results)}/{len(raw_results)}" if raw_results else "0/0"
            }
        }
        
        # Cache the result for future use in this session (keyed by KB ID + query)
        _query_cache[cache_key] = final_response.copy()
        logger.info(f"QUERY_CACHE_STORE: Cached results for KB={knowledge_base_id}, query_hash={cache_key.split(':')[1]}")
        


        
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


def _redline_pdf_document(
    agent_processing_bucket: str,
    agent_document_key: str,
    redline_items: List[Dict[str, str]],
    document_s3_key: str,
    session_id: str,
    user_id: str
) -> Dict[str, Any]:
    """Handle PDF redlining using annotation-based approach."""
    try:
        # Download PDF from S3
        response = s3_client.get_object(Bucket=agent_processing_bucket, Key=agent_document_key)
        pdf_bytes = response['Body'].read()
        
        # Get PDF processor
        pdf_processor = get_pdf_processor()
        if not pdf_processor:
            # Cleanup on failure
            if session_id and user_id:
                try:
                    _cleanup_session_documents(session_id, user_id)
                except Exception as cleanup_error:
                    logger.error(f"Session cleanup error during PDF processor failure: {cleanup_error}")
            return {
                "success": False,
                "error": "PDF processor not available"
            }
        
        logger.info(f"PDF_REDLINE: Processing {len(redline_items)} conflicts")
        
        # Find conflicts in PDF
        position_mapping = {}
        for conflict in redline_items:
            conflict_text = conflict.get('text', '').strip()
            if conflict_text:
                matches = pdf_processor.find_text_in_pdf(pdf_bytes, conflict_text, fuzzy=True)
                logger.info(f"PDF_REDLINE: Conflict text '{conflict_text[:50]}...' -> {len(matches)} matches")
                if matches:
                    position_mapping[conflict_text] = matches
                else:
                    logger.warning(f"PDF_REDLINE: NO MATCHES found for conflict: '{conflict_text[:100]}...'")
        
        # Create redlined PDF
        redlined_pdf = pdf_processor.redline_pdf(pdf_bytes, redline_items, position_mapping)
        
        # Save redlined PDF
        redlined_s3_key = _create_redlined_filename(agent_document_key, session_id, user_id)
        
        # Upload redlined PDF to S3
        s3_client.put_object(
            Bucket=agent_processing_bucket,
            Key=redlined_s3_key,
            Body=redlined_pdf,
            ContentType='application/pdf',
            Metadata={
                'original_document': document_s3_key,
                'agent_document': agent_document_key,
                'redlined_by': 'Legal-AI',
                'conflicts_count': str(len(redline_items)),
                'matches_found': str(len(position_mapping))
            }
        )
        
        logger.info(f"PDF_REDLINE_COMPLETE: Uploaded to {redlined_s3_key}")
        
        # Handle cleanup if needed
        cleanup_result = None
        if session_id and user_id:
            try:
                cleanup_result = _cleanup_session_documents(session_id, user_id)
            except Exception as cleanup_error:
                logger.error(f"Session cleanup error: {cleanup_error}")
        
        # Collect page numbers for paragraphs_with_redlines
        pages_with_redlines = list(set([m['page_number'] for matches in position_mapping.values() for m in matches]))
        
        return {
            "success": True,
            "original_document": document_s3_key,
            "agent_document": agent_document_key,
            "redlined_document": redlined_s3_key,
            "conflicts_processed": len(redline_items),
            "matches_found": len(position_mapping),
            "paragraphs_with_redlines": pages_with_redlines,
            "bucket": agent_processing_bucket,
            "file_type": "pdf",
            "cleanup_performed": cleanup_result is not None,
            "message": f"Successfully created redlined PDF with {len(position_mapping)} conflicts annotated"
        }
        
    except Exception as e:
        logger.error(f"Error in PDF redlining: {str(e)}")
        # Cleanup on failure
        if session_id and user_id:
            try:
                _cleanup_session_documents(session_id, user_id)
            except Exception as cleanup_error:
                logger.error(f"Session cleanup error during PDF redlining exception: {cleanup_error}")
        return {
            "success": False,
            "error": str(e),
            "original_document": document_s3_key
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
    Handles both DOCX and PDF documents with appropriate processing methods.
    
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
        
        # Detect file type - route to appropriate processor
        is_pdf = is_pdf_file(document_s3_key) if PDF_SUPPORT_ENABLED else False
        logger.info(f"FILE_TYPE_DETECTED: {'PDF' if is_pdf else 'DOCX'}")
        
        # Get bucket configurations
        source_bucket = _get_bucket_name(bucket_type)
        agent_processing_bucket = os.environ.get('AGENT_PROCESSING_BUCKET')
        
        if not agent_processing_bucket or not source_bucket:
            # Cleanup on failure
            if session_id and user_id:
                try:
                    _cleanup_session_documents(session_id, user_id)
                except Exception as cleanup_error:
                    logger.error(f"Session cleanup error during bucket config check: {cleanup_error}")
            return {
                "success": False,
                "error": "Required buckets not configured"
            }
        

        
        # Step 1: Copy document to agent processing bucket
        agent_document_key = _copy_document_to_processing(document_s3_key, source_bucket, agent_processing_bucket)
        
        # Step 3: Parse conflicts and create redline items from analysis data
        redline_items = parse_conflicts_for_redlining(analysis_data)
        logger.info(f"REDLINE_PARSE: Found {len(redline_items)} conflicts to redline")
        if redline_items:
            logger.info(f"REDLINE_PARSE: First conflict preview: '{redline_items[0].get('text', '')[:100]}...'")
        
        # Step 3.5: Assign sequential serial numbers based on comment content deduplication
        # Track seen comments by normalized content to prevent duplicates and assign serial numbers
        seen_comments = {}  # Key: normalized comment content, Value: serial_number
        serial_number = 0
        
        for item in redline_items:
            comment = item.get('comment', '').strip()
            if not comment:
                continue
            
            # Normalize comment content for duplicate detection (remove serial numbers if present)
            # Extract the core content: everything after "CONFLICT" but before any existing serial number
            # Preserve the Reference section for normalization
            normalized_comment = comment
            # Remove any existing conflict ID pattern to get core content (but keep Reference section)
            normalized_comment = re.sub(r'^CONFLICT\s+\S+\s*\(', 'CONFLICT (', normalized_comment)
            normalized_comment = normalized_comment.strip()
            
            # Check if we've seen this comment content before
            if normalized_comment not in seen_comments:
                serial_number += 1
                seen_comments[normalized_comment] = serial_number
                # Update comment with serial number
                # Format: "CONFLICT {serial_number} ({type}): {rationale}\n\nReference: {source_doc} ({clause_ref})"
                # Extract type and everything after (including rationale and Reference section)
                match = re.match(r'^CONFLICT\s+\S+\s*\(([^)]+)\):\s*(.+)', comment, re.DOTALL)
                if match:
                    conflict_type = match.group(1)
                    rationale_and_ref = match.group(2)  # This includes rationale + Reference section
                    item['comment'] = f"CONFLICT {serial_number} ({conflict_type}): {rationale_and_ref}"
                else:
                    # Fallback: just prepend serial number, preserve entire comment including Reference
                    item['comment'] = f"CONFLICT {serial_number}: {comment.replace('CONFLICT ', '').replace('CONFLICT', '')}"
                item['serial_number'] = serial_number
                logger.debug(f"Assigned serial number {serial_number} to conflict with comment: {normalized_comment[:100]}...")
            else:
                # Duplicate comment - use existing serial number
                existing_serial = seen_comments[normalized_comment]
                match = re.match(r'^CONFLICT\s+\S+\s*\(([^)]+)\):\s*(.+)', comment, re.DOTALL)
                if match:
                    conflict_type = match.group(1)
                    rationale_and_ref = match.group(2)  # This includes rationale + Reference section
                    item['comment'] = f"CONFLICT {existing_serial} ({conflict_type}): {rationale_and_ref}"
                else:
                    item['comment'] = f"CONFLICT {existing_serial}: {comment.replace('CONFLICT ', '').replace('CONFLICT', '')}"
                item['serial_number'] = existing_serial
                logger.info(f"DUPLICATE_COMMENT: Found duplicate comment content, reusing serial number {existing_serial}")
        
        logger.info(f"REDLINE_SERIAL: Assigned {serial_number} unique serial numbers to {len(redline_items)} conflicts (based on comment content deduplication)")
        
        if not redline_items:
            # Cleanup reference documents when no conflicts detected
            cleanup_result = None
            if session_id and user_id:
                try:
                    cleanup_result = _cleanup_session_documents(session_id, user_id)
                    logger.info(f"Cleanup completed for 0 conflicts case: {cleanup_result}")
                except Exception as cleanup_error:
                    logger.error(f"Session cleanup error during no conflicts check: {cleanup_error}")
            return {
                "success": True,
                "no_conflicts": True,
                "message": "No conflicts found in analysis data for redlining",
                "cleanup_performed": cleanup_result is not None,
                "cleanup_result": cleanup_result
            }
        
        # Route to appropriate processor based on file type
        if is_pdf and PDF_SUPPORT_ENABLED:
            logger.info("PROCESSING_PDF: Converting PDF to DOCX first, then processing as DOCX")
            # Convert PDF to DOCX before processing
            try:
                converted_docx_key = _convert_pdf_to_docx_in_processing_bucket(
                    agent_processing_bucket,
                    agent_document_key
                )
                logger.info(f"PDF_TO_DOCX_COMPLETE: Converted PDF to DOCX: {converted_docx_key}")
                # Update agent_document_key to use the converted DOCX
                agent_document_key = converted_docx_key
            except Exception as convert_error:
                logger.error(f"PDF_TO_DOCX_FAILED: {str(convert_error)}, falling back to PDF annotation")
                # Fallback to PDF annotation if conversion fails
                return _redline_pdf_document(
                    agent_processing_bucket,
                    agent_document_key,
                    redline_items,
                    document_s3_key,
                    session_id,
                    user_id
                )
        
        # DOCX Processing (either original DOCX or converted from PDF)
        logger.info("PROCESSING_DOCX: Using DOCX text modification redlining")
        
        # DOCX Processing - Original code path
        # Step 2: Download and load the DOCX document
        doc = _download_and_load_document(agent_processing_bucket, agent_document_key)
        
        # Debug logging: Log document structure for troubleshooting
        logger.info(f"DOCUMENT_DEBUG: Loaded document with {len(doc.paragraphs)} paragraphs")
        for i, para in enumerate(doc.paragraphs[:5]):  # Log first 5 paragraphs
            if para.text.strip():
                logger.info(f"DOCUMENT_DEBUG: Para {i}: '{para.text[:100]}...'")
        
        # Note: redline_items was already parsed above, no need to parse again
        # If we reach here, redline_items should not be empty (checked earlier)
        # But add a safety check to prevent crashes
        if not redline_items:
            # This should not happen since we checked earlier, but handle gracefully
            logger.warning("REDLINE_WARNING: redline_items is empty after document load, this should not happen")
            cleanup_result = None
            if session_id and user_id:
                try:
                    cleanup_result = _cleanup_session_documents(session_id, user_id)
                    logger.info(f"Cleanup completed for unexpected 0 conflicts case: {cleanup_result}")
                except Exception as cleanup_error:
                    logger.error(f"Session cleanup error during unexpected no conflicts check: {cleanup_error}")
            return {
                "success": True,
                "no_conflicts": True,
                "message": "No conflicts found in analysis data for redlining",
                "cleanup_performed": cleanup_result is not None,
                "cleanup_result": cleanup_result
            }
        
        # Log first conflict preview safely
        if redline_items:
            logger.info(f"REDLINE_PARSE: First conflict preview: '{redline_items[0].get('text', '')[:100]}...'")
        
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
        logger.info(f"REDLINE_UPLOAD: Uploaded redlined document to {redlined_s3_key}")
        
        if not upload_success:
            # Cleanup on failure
            if session_id and user_id:
                try:
                    _cleanup_session_documents(session_id, user_id)
                except Exception as cleanup_error:
                    logger.error(f"Session cleanup error during upload failure: {cleanup_error}")
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
        # Cleanup on failure
        if session_id and user_id:
            try:
                _cleanup_session_documents(session_id, user_id)
            except Exception as cleanup_error:
                logger.error(f"Session cleanup error during redlining workflow exception: {cleanup_error}")
        return {
            "success": False,
            "error": str(e),
            "original_document": document_s3_key
        }


def parse_conflicts_for_redlining(analysis_data: str) -> List[Dict[str, str]]:
    """
    Parse conflicts data for redlining. Supports both JSON object format (with explanation and conflicts),
    JSON array format, and markdown table format.
    Extracts exact sentences from vendor_quote field that should be present in vendor document.
    
    Args:
        analysis_data: Analysis string containing JSON object/array or markdown table
        
    Returns:
        List of redline items with exact text to match and comments
    """
    logger.info(f"PARSE_START: Analysis data length: {len(analysis_data)} characters")
    logger.info(f"PARSE_START: Analysis preview: {analysis_data[:150]}...")

    redline_items = []
    explanation = ""
    
    try:
        # Try to parse as JSON first (new format)
        import json
        import re
        
        # First, try to find JSON object pattern (new format with explanation and conflicts)
        json_object_match = re.search(r'\{[\s\S]*"conflicts"[\s\S]*\}', analysis_data)
        if json_object_match:
            try:
                json_str = json_object_match.group(0)
                parsed_json = json.loads(json_str)
                
                # Check if it's the new structure with explanation and conflicts
                if isinstance(parsed_json, dict) and "conflicts" in parsed_json:
                    explanation = parsed_json.get("explanation", "")
                    conflicts_json = parsed_json.get("conflicts", [])
                    
                    # Log the explanation
                    if explanation:
                        logger.info(f"PARSE_EXPLANATION: {explanation}")
                    else:
                        logger.info("PARSE_EXPLANATION: No explanation provided")
                    
                    if isinstance(conflicts_json, list):
                        logger.info(f"PARSE_JSON: Found JSON object with explanation and {len(conflicts_json)} conflicts")
                    else:
                        logger.warning(f"PARSE_JSON: Expected conflicts to be a list, got {type(conflicts_json)}")
                        conflicts_json = []
                else:
                    # Not the expected structure, fall through to array parsing
                    conflicts_json = None
            except json.JSONDecodeError as e:
                logger.warning(f"PARSE_JSON_OBJECT_FAILED: Could not parse as JSON object, trying array format: {e}")
                conflicts_json = None
        else:
            conflicts_json = None
        
        # If we didn't find the new structure, try to find JSON array pattern (backwards compatibility)
        if conflicts_json is None:
            json_match = re.search(r'\[[\s\S]*\]', analysis_data)
            if json_match:
                try:
                    json_str = json_match.group(0)
                    conflicts_json = json.loads(json_str)
                    
                    if isinstance(conflicts_json, list):
                        logger.info(f"PARSE_JSON: Found JSON array with {len(conflicts_json)} conflicts (backwards compatibility mode)")
                    else:
                        conflicts_json = None
                except json.JSONDecodeError as e:
                    logger.warning(f"PARSE_JSON_ARRAY_FAILED: Could not parse as JSON array: {e}")
                    conflicts_json = None
        
        # Process conflicts if we found them
        if conflicts_json is not None and isinstance(conflicts_json, list):
                    
                    validated_count = 0
                    validation_errors = []
                    
                    for idx, conflict in enumerate(conflicts_json):
                        if not isinstance(conflict, dict):
                            logger.warning(f"PARSE_SKIP: Conflict {idx} is not a dict, skipping")
                            continue
                        
                        # Log raw conflict data before processing
                        conflict_id = conflict.get('clarification_id', f'Unknown-{idx}')
                        raw_vendor_quote = conflict.get('vendor_quote', '')
                        logger.info(f"PARSE_RAW_CONFLICT: ID={conflict_id}, vendor_quote type={type(raw_vendor_quote)}, length={len(str(raw_vendor_quote))}")
                        logger.info(f"PARSE_RAW_CONFLICT: vendor_quote (first 200 chars)='{str(raw_vendor_quote)[:200]}...'")
                        if '\\' in str(raw_vendor_quote):
                            logger.info(f"PARSE_RAW_CONFLICT: vendor_quote contains backslashes, count={str(raw_vendor_quote).count('\\\\')}")
                        
                        # Use Pydantic validation if available
                        if PYDANTIC_AVAILABLE and ConflictModel:
                            try:
                                validated_conflict = ConflictModel(**conflict)
                                
                                # Log after Pydantic validation
                                logger.info(f"PARSE_AFTER_PYDANTIC: ID={validated_conflict.clarification_id}, vendor_quote type={type(validated_conflict.vendor_quote)}, length={len(validated_conflict.vendor_quote)}")
                                logger.info(f"PARSE_AFTER_PYDANTIC: vendor_quote (first 200 chars)='{validated_conflict.vendor_quote[:200]}...'")
                                
                                # Use comment format: CONFLICT ID (type): rationale
                                comment = f"CONFLICT {validated_conflict.clarification_id} ({validated_conflict.conflict_type}): {validated_conflict.rationale}"
                                
                                # Add reference section
                                if validated_conflict.source_doc and validated_conflict.source_doc.strip():
                                    comment += f"\n\nReference: {validated_conflict.source_doc.strip()}"
                                    if validated_conflict.clause_ref and validated_conflict.clause_ref.strip() and validated_conflict.clause_ref.lower() not in ['n/a', 'na', 'none']:
                                        comment += f" ({validated_conflict.clause_ref.strip()})"
                                
                                # Store original vendor_quote (only normalize escaped quotes for JSON parsing)
                                # Full normalization will happen during matching to preserve original structure
                                vendor_quote_before_norm = validated_conflict.vendor_quote.strip()
                                vendor_quote_text = normalize_escaped_quotes(vendor_quote_before_norm)
                                logger.info(f"PARSE_NORMALIZE: ID={validated_conflict.clarification_id}, before normalize_escaped_quotes='{vendor_quote_before_norm[:150]}...'")
                                logger.info(f"PARSE_NORMALIZE: ID={validated_conflict.clarification_id}, after normalize_escaped_quotes='{vendor_quote_text[:150]}...'")
                                if vendor_quote_before_norm != vendor_quote_text:
                                    logger.info(f"PARSE_NORMALIZE: ID={validated_conflict.clarification_id}, normalization changed the text")
                                
                                # Validate for truncated quotes
                                is_truncated = _validate_vendor_quote_completeness(vendor_quote_text, validated_conflict.clarification_id)
                                if is_truncated:
                                    logger.warning(f"PARSE_VALIDATION: ID={validated_conflict.clarification_id}, vendor_quote appears to be TRUNCATED/INCOMPLETE. Length={len(vendor_quote_text)}, ends_with='{vendor_quote_text[-30:]}'")
                                
                                redline_items.append({
                                    'text': vendor_quote_text,
                                    'comment': comment,
                                    'author': 'Legal-AI',
                                    'initials': 'LAI',
                                    'clarification_id': validated_conflict.clarification_id,
                                    'conflict_type': validated_conflict.conflict_type,
                                    'source_doc': validated_conflict.source_doc,
                                    'clause_ref': validated_conflict.clause_ref,
                                    'summary': validated_conflict.summary,
                                    'rationale': validated_conflict.rationale
                                })
                                validated_count += 1
                                
                            except Exception as e:
                                error_msg = f"Conflict {idx} validation failed: {str(e)}"
                                validation_errors.append(error_msg)
                                logger.warning(f"PARSE_VALIDATION_ERROR: {error_msg}")
                                # Fall through to manual validation for backwards compatibility
                                pass
                        
                        # Fallback to manual validation if Pydantic not available or validation failed
                        if not (PYDANTIC_AVAILABLE and ConflictModel):
                            clarification_id = conflict.get('clarification_id', '')
                            vendor_quote = conflict.get('vendor_quote', '')
                            summary = conflict.get('summary', '')
                            source_doc = conflict.get('source_doc', '')
                            clause_ref = conflict.get('clause_ref', '')
                            conflict_type = conflict.get('conflict_type', '')
                            rationale = conflict.get('rationale', '')
                            
                            logger.info(f"PARSE_MANUAL: ID={clarification_id}, vendor_quote type={type(vendor_quote)}, length={len(str(vendor_quote))}")
                            logger.info(f"PARSE_MANUAL: vendor_quote (first 200 chars)='{str(vendor_quote)[:200]}...'")
                            
                            # Clean up vendor quote - remove surrounding quotes if present
                            vendor_quote_clean = str(vendor_quote).strip()
                            if vendor_quote_clean.startswith('"') and vendor_quote_clean.endswith('"'):
                                logger.info(f"PARSE_MANUAL: ID={clarification_id}, removing surrounding quotes")
                                vendor_quote_clean = vendor_quote_clean[1:-1]
                            
                            # Only normalize escaped quotes - full normalization happens during matching
                            vendor_quote_before_norm = vendor_quote_clean
                            vendor_quote_clean = normalize_escaped_quotes(vendor_quote_clean)
                            logger.info(f"PARSE_MANUAL_NORM: ID={clarification_id}, before='{vendor_quote_before_norm[:150]}...', after='{vendor_quote_clean[:150]}...'")
                            if vendor_quote_before_norm != vendor_quote_clean:
                                logger.info(f"PARSE_MANUAL_NORM: ID={clarification_id}, normalization changed the text")
                            
                            # Create redline item using exact vendor quote for matching
                            if vendor_quote_clean.strip():  # Only add if we have actual text
                                # Use comment format: CONFLICT ID (type): rationale
                                comment = f"CONFLICT {clarification_id} ({conflict_type}): {rationale}"
                                
                                # Add reference section if source_doc is provided
                                if source_doc and str(source_doc).strip():
                                    comment += f"\n\nReference: {str(source_doc).strip()}"
                                    if clause_ref and str(clause_ref).strip() and str(clause_ref).lower() not in ['n/a', 'na', 'none']:
                                        comment += f" ({str(clause_ref).strip()})"
                                
                                redline_items.append({
                                    'text': vendor_quote_clean.strip(),
                                    'comment': comment,
                                    'author': 'Legal-AI',
                                    'initials': 'LAI',
                                    'clarification_id': str(clarification_id),
                                    'conflict_type': str(conflict_type),
                                    'source_doc': str(source_doc),
                                    'clause_ref': str(clause_ref),
                                    'summary': str(summary),
                                    'rationale': str(rationale)
                                })
                    
                    if PYDANTIC_AVAILABLE and ConflictModel:
                        logger.info(f"PARSE_VALIDATION: Validated {validated_count}/{len(conflicts_json)} conflicts using Pydantic")
                        if validation_errors:
                            logger.warning(f"PARSE_VALIDATION: {len(validation_errors)} validation errors occurred")
                    
                    # Deduplicate and filter using composite key (clarification_id + vendor_quote text)
                    # This ensures conflicts with same ID but different text (from different chunks) are kept
                    seen_conflicts = {}  # Key: (clarification_id, normalized_text)
                    deduplicated_items = []
                    for item in redline_items:
                        clarification_id = item.get('clarification_id', '')
                        text_val = (item.get('text') or '').strip()
                        # Filter placeholders/empty like 'N/A' or too short strings
                        if not text_val or text_val.lower() in ['n/a', 'na', 'none', 'n.a.', 'n.a', 'not available'] or len(text_val) < 5:
                            logger.warning(f"PARSE_FILTER: Skipping placeholder/empty conflict for ID={clarification_id} text='{text_val}'")
                            continue
                        
                        # Create composite key: clarification_id + normalized text (first 100 chars for uniqueness)
                        normalized_text = text_val.lower().strip()[:100]  # Normalize and truncate for key
                        composite_key = (clarification_id, normalized_text)
                        
                        if composite_key not in seen_conflicts:
                            seen_conflicts[composite_key] = True
                            deduplicated_items.append(item)
                        else:
                            logger.warning(f"PARSE_DEDUP: Duplicate conflict found - ID={clarification_id}, text_preview='{text_val[:50]}...', skipping")
                    
                    logger.info(f"PARSE_COMPLETE: Parsed {len(redline_items)} conflicts from JSON, {len(deduplicated_items)} unique conflicts after deduplication (using composite key: clarification_id + text)")
                    if len(redline_items) != len(deduplicated_items):
                        dropped = len(redline_items) - len(deduplicated_items)
                        logger.info(f"PARSE_DEDUP_SUMMARY: Dropped {dropped} duplicate conflicts (same ID + text combination)")
                    
                    # Store explanation in a way that can be accessed later if needed
                    # For now, we just log it - conflicts are what we return
                    return deduplicated_items
        
        # Fallback to markdown table parsing (backwards compatibility)
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
                    
                    # Only normalize escaped quotes - full normalization happens during matching
                    vendor_quote_clean = normalize_escaped_quotes(vendor_quote_clean)
                    
                    # Create redline item using exact vendor quote for matching
                    if vendor_quote_clean.strip():  # Only add if we have actual text
                        # Use Ritik's comment format from main branch: CONFLICT ID (type): rationale
                        comment = f"CONFLICT {clarification_id} ({conflict_type}): {rationale}"
                        
                        # Add reference section if source_doc is provided
                        if source_doc and source_doc.strip():
                            comment += f"\n\nReference: {source_doc.strip()}"
                            if clause_ref and clause_ref.strip() and clause_ref.lower() not in ['n/a', 'na', 'none']:
                                comment += f" ({clause_ref.strip()})"
                        
                        redline_items.append({
                            'text': vendor_quote_clean.strip(),  # Exact sentence from vendor document
                            'comment': comment,
                            'author': 'Legal-AI',
                            'initials': 'LAI',
                            'clarification_id': clarification_id,
                            'conflict_type': conflict_type,
                            'source_doc': source_doc,
                            'clause_ref': clause_ref,
                            'summary': summary,
                            'rationale': rationale
                        })
                        
        # Deduplicate conflicts using composite key (clarification_id + vendor_quote text)
        # This ensures conflicts with same ID but different text (from different chunks) are kept
        seen_conflicts = {}  # Key: (clarification_id, normalized_text)
        deduplicated_items = []
        for item in redline_items:
            clarification_id = item.get('clarification_id', '')
            text_val = (item.get('text') or '').strip()
            # Only filter truly empty or placeholder values - be more permissive with short text
            # User preference: better to include conflicts than lose them
            if not text_val or text_val.lower() in ['n/a', 'na', 'none', 'n.a.', 'n.a', 'not available']:
                logger.warning(f"PARSE_FILTER: Skipping placeholder conflict for ID={clarification_id} text='{text_val}'")
                continue
            # Allow very short text (minimum 3 chars instead of 5) - user wants to preserve conflicts
            if len(text_val) < 3:
                logger.warning(f"PARSE_FILTER: Skipping extremely short conflict for ID={clarification_id} text='{text_val}' (length: {len(text_val)})")
                continue
            
            # Create composite key: clarification_id + normalized text (first 100 chars for uniqueness)
            normalized_text = text_val.lower().strip()[:100]  # Normalize and truncate for key
            composite_key = (clarification_id, normalized_text)
            
            if composite_key not in seen_conflicts:
                seen_conflicts[composite_key] = True
                deduplicated_items.append(item)
            else:
                logger.warning(f"PARSE_DEDUP: Duplicate conflict found - ID={clarification_id}, text_preview='{text_val[:50]}...', skipping")
        
        logger.info(f"PARSE_COMPLETE: Parsed {len(redline_items)} conflicts from analysis, {len(deduplicated_items)} unique conflicts after deduplication (using composite key: clarification_id + text)")
        if len(redline_items) != len(deduplicated_items):
            dropped = len(redline_items) - len(deduplicated_items)
            logger.info(f"PARSE_DEDUP_SUMMARY: Dropped {dropped} duplicate conflicts (same ID + text combination)")
        for i, item in enumerate(deduplicated_items[:2]):
            logger.info(f"PARSE_CONFLICT_{i+1}: ID={item.get('clarification_id')}, Text='{item.get('text', '')[:60]}...'")

        # Return deduplicated list
        redline_items = deduplicated_items
        
    except Exception as e:
        logger.error(f"Error parsing conflicts for redlining: {str(e)}")
    
    return redline_items


def _validate_vendor_quote_completeness(vendor_quote: str, clarification_id: str) -> bool:
    """
    Validate if vendor quote appears to be complete or truncated.
    
    Checks for signs that the quote was cut off mid-sentence or is incomplete.
    
    Args:
        vendor_quote: The vendor quote text to validate
        clarification_id: ID of the conflict for logging
        
    Returns:
        True if quote appears truncated, False if complete
    """
    if not vendor_quote or len(vendor_quote.strip()) < 10:
        return True  # Too short to be meaningful
    
    stripped = vendor_quote.rstrip()
    
    # Check if quote ends with proper punctuation (complete sentence/clause)
    ends_with_punctuation = stripped.endswith(('.', '!', '?', ';', ':', ')', ']', '}'))
    
    # Check if quote ends with common connecting words (likely truncated)
    common_end_words = ('or', 'and', 'the', 'a', 'an', 'to', 'of', 'in', 'for', 'with', 'from', 'by', 'at', 'on', 'as', 'is', 'are', 'was', 'were', 'be', 'been', 'have', 'has', 'had', 'do', 'does', 'did', 'will', 'would', 'could', 'should', 'may', 'might', 'must', 'can')
    ends_with_common_word = any(stripped.lower().endswith(f' {word}') or stripped.lower().endswith(f', {word}') or stripped.lower().endswith(f'; {word}') for word in common_end_words)
    
    # Check if quote is suspiciously short (less than 100 chars is often incomplete for legal clauses)
    is_short = len(vendor_quote) < 100
    
    # Check if quote ends mid-word or with incomplete phrase
    ends_mid_phrase = (
        not ends_with_punctuation and 
        (ends_with_common_word or is_short)
    )
    
    is_truncated = ends_mid_phrase or (is_short and not ends_with_punctuation)
    
    if is_truncated:
        logger.warning(f"VALIDATE_TRUNCATED: ID={clarification_id}, quote appears truncated. "
                      f"Length={len(vendor_quote)}, ends_with_punctuation={ends_with_punctuation}, "
                      f"ends_with_common_word={ends_with_common_word}, is_short={is_short}, "
                      f"last_30_chars='{vendor_quote[-30:]}'")
    
    return is_truncated


def normalize_escaped_quotes(text: str) -> str:
    """
    Normalize escaped quotes in vendor_quote to match document text.
    
    Handles cases where vendor_quote contains literal \" or \' that need to match
    document text with regular " or ' quotes.
    
    Since json.loads() already handles JSON escapes, if we see \"
    it means literal backslash+quote. We normalize to just quote.
    
    Args:
        text: Text that may contain escaped quotes
        
    Returns:
        Text with escaped quotes normalized to regular quotes
    """
    if not text:
        return text
    
    original_text = text
    had_backslashes = '\\' in text
    
    # Replace literal \" with " (backslash followed by quote)
    # This handles cases like: \"word\" -> "word"
    text = text.replace('\\"', '"')
    # Replace literal \' with ' (backslash followed by single quote)
    text = text.replace("\\'", "'")
    # Handle unicode escapes (though these should be handled by JSON parsing)
    text = text.replace('\\u0022', '"')  # Unicode double quote
    text = text.replace('\\u0027', "'")  # Unicode single quote
    
    # Also handle cases where backslash appears before curly quotes (shouldn't happen after JSON parsing, but handle it)
    text = text.replace('\\"', '"')  # Escaped left curly quote
    text = text.replace('\\"', '"')  # Escaped right curly quote
    
    # Log if normalization changed anything
    if had_backslashes and text != original_text:
        logger.info(f"NORMALIZE_ESCAPED_QUOTES: Changed text. Before (first 100): '{original_text[:100]}...', After (first 100): '{text[:100]}...'")
        logger.info(f"NORMALIZE_ESCAPED_QUOTES: Backslash count before={original_text.count('\\\\')}, after={text.count('\\\\')}")
        # Show where backslashes were found (first few occurrences)
        backslash_count = 0
        for i, char in enumerate(original_text):
            if char == '\\' and i < len(original_text) - 1 and backslash_count < 3:
                next_char = original_text[i+1]
                logger.debug(f"NORMALIZE_ESCAPED_QUOTES: Found backslash at pos {i}, followed by '{next_char}' (U+{ord(next_char):04X})")
                backslash_count += 1
    
    return text


def normalize_quotes(text: str) -> str:
    """
    Normalize various quote characters to standard ASCII quotes.
    
    Handles curly quotes, smart quotes, and other quote variants
    that may differ between LLM output and document text.
    
    This function is idempotent - calling it multiple times produces the same result.
    
    Args:
        text: Text with various quote characters
        
    Returns:
        Text with normalized ASCII quotes
    """
    if not text:
        return text
    
    original_text = text
    had_curly_quotes = '"' in text or '"' in text or ''' in text or ''' in text
    
    # Normalize double quotes (curly, smart, etc.) to standard "
    # Order matters: normalize curly quotes first, then other variants
    text = text.replace('"', '"')  # Left double quotation mark (U+201C)
    text = text.replace('"', '"')  # Right double quotation mark (U+201D)
    text = text.replace('„', '"')  # Double low-9 quotation mark (U+201E)
    text = text.replace('«', '"')  # Left-pointing double angle quotation (U+00AB)
    text = text.replace('»', '"')  # Right-pointing double angle quotation (U+00BB)
    
    # Normalize single quotes (curly, smart, etc.) to standard '
    text = text.replace(''', "'")  # Left single quotation mark (U+2018)
    text = text.replace(''', "'")  # Right single quotation mark (U+2019)
    text = text.replace('‚', "'")  # Single low-9 quotation mark (U+201A)
    text = text.replace('‹', "'")  # Single left-pointing angle quotation (U+2039)
    text = text.replace('›', "'")  # Single right-pointing angle quotation (U+203A)
    
    # Also handle any remaining non-ASCII quote-like characters
    # This is a catch-all for any Unicode quote variants we might have missed
    result_chars = []
    for char in text:
        # Check if character is a quote-like character
        if unicodedata.category(char) in ('Pi', 'Pf'):  # Initial/Final punctuation (quotes)
            # Convert to standard quote
            if char in ['"', '"', '«', '»', '„']:
                result_chars.append('"')
            elif char in [''', ''', '‹', '›', '‚']:
                result_chars.append("'")
            else:
                result_chars.append(char)  # Keep as-is if we don't recognize it
        else:
            result_chars.append(char)
    
    text = ''.join(result_chars)
    
    # Log if normalization changed anything (only for significant changes to avoid spam)
    if had_curly_quotes and text != original_text:
        # Find first difference
        for i, (orig_char, norm_char) in enumerate(zip(original_text, text)):
            if orig_char != norm_char:
                logger.debug(f"NORMALIZE_QUOTES: Changed at position {i}. Before: '{orig_char}' (U+{ord(orig_char):04X}), After: '{norm_char}' (U+{ord(norm_char):04X})")
                logger.debug(f"NORMALIZE_QUOTES: Context (first 100): '{original_text[:100]}...' -> '{text[:100]}...'")
                break
    
    return text


def normalize_whitespace(text: str) -> str:
    """
    Normalize all whitespace characters to single spaces.
    
    Handles newlines, tabs, multiple spaces, and other whitespace
    that may differ between LLM output and document text.
    
    Args:
        text: Text with various whitespace characters
        
    Returns:
        Text with normalized whitespace (single spaces)
    """
    if not text:
        return text
    
    # Replace all whitespace (newlines, tabs, multiple spaces) with single space
    return re.sub(r'\s+', ' ', text).strip()


def normalize_for_matching(text: str) -> str:
    """
    Apply all normalizations to prepare text for matching.
    
    Combines: escaped quotes, quote types, and whitespace normalization.
    
    Args:
        text: Text to normalize
        
    Returns:
        Fully normalized text ready for matching
    """
    if not text:
        return text
    
    text = normalize_escaped_quotes(text)
    text = normalize_quotes(text)
    text = normalize_whitespace(text)
    return text


def apply_exact_sentence_redlining(doc, redline_items: List[Dict[str, str]]) -> Dict[str, Any]:
    """
    Apply redlining to document with PRECISE matching.
    
    PRECISION APPROACH:
    - Only redlines EXACT vendor_quote text (with normalization fallbacks)
    - Handles multiple conflicts in the same paragraph
    - Comments are anchored to the exact runs containing the text
    - Duplicates detected by vendor_quote field only
    - Normalizes: escaped quotes, quote types, whitespace
    - Supports cross-paragraph matching for text split across paragraphs
    
    Args:
        doc: python-docx Document object
        redline_items: List of conflict items with 'text' (vendor_quote) to highlight
        
    Returns:
        Dictionary with redlining results
    """
    logger.info(f"APPLY_START: Processing {len(redline_items)} conflicts across {len(doc.paragraphs)} paragraphs")
    
    try:
        total_paragraphs = len(doc.paragraphs)
        failed_matches = []
        
        # Document structure logging
        logger.info(f"DOCUMENT_STRUCTURE: Total paragraphs: {total_paragraphs}")
        
        # Track seen vendor_quotes for duplicate detection (normalize for comparison)
        seen_vendor_quotes = set()
        
        def normalize_vendor_quote_for_dedup(text: str) -> str:
            """Normalize vendor_quote for duplicate detection."""
            if not text:
                return ""
            return normalize_for_matching(text).lower()
        
        # PHASE 1: Pre-scan - find all matches for all conflicts
        # This allows us to handle multiple conflicts per paragraph
        paragraph_matches = {}  # para_idx -> list of (start_pos, end_pos, comment, vendor_quote, conflict_id)
        cross_para_matches = []  # List of cross-paragraph matches to handle separately
        
        for redline_item in redline_items:
            vendor_quote = redline_item.get('text', '').strip()
            
            if not vendor_quote:
                logger.warning("SKIP: Empty vendor_quote")
                continue
            
            # Log vendor quote as retrieved from redline_items
            conflict_id = redline_item.get('id', redline_item.get('clarification_id', 'Unknown'))
            logger.info(f"SCANNING_RETRIEVED: ID={conflict_id}, vendor_quote type={type(vendor_quote)}, length={len(vendor_quote)}")
            logger.info(f"SCANNING_RETRIEVED: vendor_quote (first 200 chars)='{vendor_quote[:200]}...'")
            logger.info(f"SCANNING_RETRIEVED: vendor_quote (last 100 chars)='...{vendor_quote[-100:]}'")
            if '\\' in vendor_quote:
                logger.warning(f"SCANNING_RETRIEVED: ID={conflict_id}, vendor_quote still contains backslashes! count={vendor_quote.count('\\\\')}")
            
            # Check for duplicates based on vendor_quote only
            normalized_quote = normalize_vendor_quote_for_dedup(vendor_quote)
            if normalized_quote in seen_vendor_quotes:
                logger.info(f"DUPLICATE_SKIP: vendor_quote already processed: '{vendor_quote[:50]}...'")
                continue
            
            seen_vendor_quotes.add(normalized_quote)
            
            # Get conflict metadata
            serial_num = redline_item.get('serial_number', 'N/A')
            comment = redline_item.get('comment', '')
            
            logger.info(f"SCANNING: Serial={serial_num}, ID={conflict_id}, vendor_quote='{vendor_quote[:80]}...'")
            logger.info(f"SCANNING_DETAIL: Full vendor_quote length={len(vendor_quote)}, first 200 chars: '{vendor_quote[:200]}'")
            
            # Find match in document using tiered strategy
            match_result = _find_text_match(doc, vendor_quote)
            
            if match_result:
                if match_result['type'] == 'single_para':
                    para_idx = match_result['para_idx']
                    if para_idx not in paragraph_matches:
                        paragraph_matches[para_idx] = []
                    paragraph_matches[para_idx].append({
                        'start_pos': match_result['start_pos'],
                        'end_pos': match_result['end_pos'],
                        'comment': comment,
                        'vendor_quote': vendor_quote,
                        'conflict_id': conflict_id,
                        'match_type': match_result['match_type']
                    })
                    logger.info(f"FOUND: {match_result['match_type']} match in paragraph {para_idx}")
                elif match_result['type'] == 'cross_para':
                    cross_para_matches.append({
                        'paragraphs': match_result['paragraphs'],
                        'comment': comment,
                        'vendor_quote': vendor_quote,
                        'conflict_id': conflict_id
                    })
                    logger.info(f"FOUND: Cross-paragraph match spanning paragraphs {match_result['paragraphs']}")
            else:
                failed_matches.append({
                    'text': vendor_quote,
                    'id': conflict_id,
                    'serial_number': serial_num
                })
                logger.warning(f"NO_MATCH: Serial={serial_num}, ID={conflict_id}, vendor_quote='{vendor_quote[:50]}...'")
        
        # PHASE 2: Apply redlines - process each paragraph with its matches
        matches_found = 0
        paragraphs_with_redlines = []
        
        for para_idx, matches in paragraph_matches.items():
            paragraph = doc.paragraphs[para_idx]
            para_text = paragraph.text
            
            # Sort matches by start position DESCENDING (process end to start to preserve positions)
            matches.sort(key=lambda x: x['start_pos'], reverse=True)
            
            logger.info(f"APPLYING: {len(matches)} redlines to paragraph {para_idx}")
            
            # Apply all redlines to this paragraph
            success = _apply_multiple_redlines(paragraph, para_text, matches)
            
            if success:
                matches_found += len(matches)
                paragraphs_with_redlines.append(para_idx)
        
        # PHASE 3: Handle cross-paragraph matches
        for cross_match in cross_para_matches:
            para_indices = cross_match['paragraphs']
            comment = cross_match['comment']
            
            # Apply redline to each paragraph in the cross-paragraph match
            for para_idx in para_indices:
                if para_idx < len(doc.paragraphs):
                    paragraph = doc.paragraphs[para_idx]
                    # Redline the entire paragraph text for cross-paragraph matches
                    _apply_full_paragraph_redline(paragraph, comment)
                    if para_idx not in paragraphs_with_redlines:
                        paragraphs_with_redlines.append(para_idx)
            
            matches_found += 1
            logger.info(f"CROSS_PARA_APPLIED: Redlined paragraphs {para_indices}")
        
        # Log results
        if paragraphs_with_redlines:
            pages_affected = set(para_idx // 20 for para_idx in paragraphs_with_redlines)
            logger.info(f"PAGE_DISTRIBUTION: {len(pages_affected)} pages affected: {sorted(pages_affected)}")
        
        total_conflicts = len(redline_items) - len([r for r in redline_items if not r.get('text', '').strip()])
        success_rate = (matches_found / total_conflicts * 100) if total_conflicts else 0
        
        if failed_matches:
            logger.warning(f"REDLINING_INCOMPLETE: {len(failed_matches)} conflicts could not be matched")
            for failed in failed_matches:
                logger.warning(f"UNMATCHED: ID={failed.get('id', 'Unknown')} - '{failed.get('text', '')[:50]}...'")
        else:
            logger.info("REDLINING_SUCCESS: All conflicts successfully matched and redlined")
        
        logger.info(f"REDLINE_RESULTS: Matches={matches_found}, Failed={len(failed_matches)}, Success rate={success_rate:.1f}%")
        
        return {
            "total_paragraphs": total_paragraphs,
            "matches_found": matches_found,
            "paragraphs_with_redlines": paragraphs_with_redlines,
            "failed_matches": failed_matches,
            "pages_affected": len(set(para_idx // 20 for para_idx in paragraphs_with_redlines)) if paragraphs_with_redlines else 0
        }
        
    except Exception as e:
        logger.error(f"Error in redlining: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        return {
            "total_paragraphs": 0,
            "matches_found": 0,
            "paragraphs_with_redlines": [],
            "error": str(e)
        }


def _find_text_match(doc, vendor_quote: str) -> Optional[Dict[str, Any]]:
    """
    Find vendor_quote text in document using tiered matching strategy.
    
    MATCHING TIERS:
    1. Exact match (original text)
    2. Quote-normalized match (handle ' vs " vs curly quotes)
    3. Quote + whitespace normalized match (handle quote and whitespace variations)
    4. Fully normalized match (quotes + whitespace + case-insensitive)
    5. Cross-paragraph match (text spans multiple paragraphs)
    
    Args:
        doc: python-docx Document object
        vendor_quote: Text to find in document
        
    Returns:
        Match result dict or None if not found
    """
    # Prepare normalized versions
    # Note: vendor_quote may already be normalized from parsing, so we normalize again (idempotent)
    logger.info(f"MATCH_PREP_START: vendor_quote length={len(vendor_quote)}, type={type(vendor_quote)}")
    logger.info(f"MATCH_PREP_START: vendor_quote (first 200)='{vendor_quote[:200]}...'")
    logger.info(f"MATCH_PREP_START: vendor_quote (last 100)='...{vendor_quote[-100:]}'")
    
    # Check for special characters
    if '\\' in vendor_quote:
        logger.warning(f"MATCH_PREP_START: vendor_quote contains backslashes! count={vendor_quote.count('\\\\')}")
    if '"' in vendor_quote or '"' in vendor_quote:
        logger.info(f"MATCH_PREP_START: vendor_quote contains curly quotes")
    
    quote_normalized = normalize_quotes(normalize_escaped_quotes(vendor_quote))
    quote_ws_normalized = normalize_whitespace(quote_normalized)
    fully_normalized = normalize_for_matching(vendor_quote)
    
    logger.info(f"MATCH_PREP: vendor_quote length={len(vendor_quote)}, quote_normalized length={len(quote_normalized)}, quote_ws_normalized length={len(quote_ws_normalized)}, fully_normalized length={len(fully_normalized)}")
    logger.info(f"MATCH_PREP: vendor_quote preview='{vendor_quote[:150]}...'")
    logger.info(f"MATCH_PREP: quote_normalized preview='{quote_normalized[:150]}...'")
    logger.info(f"MATCH_PREP: quote_ws_normalized preview='{quote_ws_normalized[:150]}...'")
    logger.info(f"MATCH_PREP: fully_normalized preview='{fully_normalized[:150]}...'")
    
    # Log if normalization changed anything
    if quote_normalized != vendor_quote:
        logger.info(f"MATCH_PREP: quote_normalized differs from vendor_quote")
    if fully_normalized != vendor_quote:
        logger.info(f"MATCH_PREP: fully_normalized differs from vendor_quote")
    
    # Search through all paragraphs
    paragraphs_checked = 0
    for para_idx, paragraph in enumerate(doc.paragraphs):
        para_text = paragraph.text
        if not para_text.strip():
            continue
        
        paragraphs_checked += 1
        # Log paragraph details for first few paragraphs or if it contains key text
        if paragraphs_checked <= 3 or 'Indemnified Party' in para_text or 'indemnify' in para_text.lower():
            logger.debug(f"MATCH_CHECK_PARA: para_idx={para_idx}, length={len(para_text)}, preview='{para_text[:150]}...'")
            if '"' in para_text or '"' in para_text:
                logger.debug(f"MATCH_CHECK_PARA: para_idx={para_idx} contains curly quotes")
        
        # TIER 1: Exact match
        if vendor_quote in para_text:
            start_pos = para_text.find(vendor_quote)
            logger.info(f"MATCH_FOUND: TIER 1 (exact) in paragraph {para_idx} at position {start_pos}")
            return {
                'type': 'single_para',
                'para_idx': para_idx,
                'start_pos': start_pos,
                'end_pos': start_pos + len(vendor_quote),
                'match_type': 'exact'
            }
        
        # TIER 2: Quote-normalized match (quotes only, preserve whitespace)
        para_quote_normalized = normalize_quotes(para_text)
        if quote_normalized in para_quote_normalized:
            start_pos = para_quote_normalized.find(quote_normalized)
            logger.info(f"MATCH_FOUND: TIER 2 (quote_normalized) in paragraph {para_idx} at position {start_pos}")
            # Map back to original positions (approximate - quotes are same length)
            return {
                'type': 'single_para',
                'para_idx': para_idx,
                'start_pos': start_pos,
                'end_pos': start_pos + len(vendor_quote),
                'match_type': 'quote_normalized'
            }
        
        # TIER 3: Quote + whitespace normalized match
        para_quotes_only = normalize_quotes(para_text)
        para_quote_ws_normalized = normalize_whitespace(para_quotes_only)
        # Log normalization for debugging (only for relevant paragraphs to avoid spam)
        if quote_ws_normalized[:50].lower() in para_quote_ws_normalized.lower():
            had_curly_before = '"' in para_text or '"' in para_text
            has_curly_after = '"' in para_quotes_only or '"' in para_quotes_only
            if had_curly_before:
                logger.info(f"MATCH_TIER3_NORM: para_idx={para_idx}, had_curly_before={had_curly_before}, has_curly_after={has_curly_after}")
                # Show actual quote characters
                for i, char in enumerate(para_text):
                    if ord(char) in [0x201C, 0x201D, 0x22]:
                        logger.info(f"MATCH_TIER3_NORM: para_idx={para_idx}, pos {i}: U+{ord(char):04X} = '{char}'")
                        break
        if quote_ws_normalized in para_quote_ws_normalized:
            # Find position in normalized text, then map back
            norm_start = para_quote_ws_normalized.find(quote_ws_normalized)
            logger.info(f"MATCH_FOUND: TIER 3 (quote_whitespace_normalized) in paragraph {para_idx} at normalized position {norm_start}")
            logger.info(f"MATCH_TIER3_DETAIL: quote_ws_normalized='{quote_ws_normalized[:100]}...', found in para_quote_ws_normalized='{para_quote_ws_normalized[norm_start:norm_start+100]}...'")
            # Map normalized position back to original position
            start_pos = _map_normalized_to_original_position(para_text, norm_start)
            end_pos = _map_normalized_to_original_position(para_text, norm_start + len(quote_ws_normalized))
            return {
                'type': 'single_para',
                'para_idx': para_idx,
                'start_pos': start_pos,
                'end_pos': end_pos,
                'match_type': 'quote_whitespace_normalized'
            }
        else:
            # Log why TIER 3 didn't match for debugging
            if len(quote_ws_normalized) > 50 and quote_ws_normalized[:50].lower() in para_quote_ws_normalized.lower():
                logger.warning(f"MATCH_TIER3_PARTIAL: First 50 chars match but full text doesn't. quote_ws_normalized length={len(quote_ws_normalized)}, para_quote_ws_normalized length={len(para_quote_ws_normalized)}")
                logger.warning(f"MATCH_TIER3_PARTIAL: quote_ws_normalized='{quote_ws_normalized[:150]}...'")
                logger.warning(f"MATCH_TIER3_PARTIAL: para_quote_ws_normalized='{para_quote_ws_normalized[:300]}...'")
        
        # TIER 4: Fully normalized + case-insensitive match
        para_fully_normalized = normalize_for_matching(para_text).lower()
        if fully_normalized.lower() in para_fully_normalized:
            norm_start = para_fully_normalized.find(fully_normalized.lower())
            logger.info(f"MATCH_FOUND: TIER 4 (fully_normalized) in paragraph {para_idx} at normalized position {norm_start}")
            start_pos = _map_normalized_to_original_position(para_text, norm_start)
            end_pos = _map_normalized_to_original_position(para_text, norm_start + len(fully_normalized))
            return {
                'type': 'single_para',
                'para_idx': para_idx,
                'start_pos': start_pos,
                'end_pos': end_pos,
                'match_type': 'fully_normalized'
            }
    
    # TIER 5: Partial/truncated quote matching
    # If vendor quote appears to be truncated (ends mid-sentence or is suspiciously short),
    # or is very long (might span paragraphs), try to match it as a prefix/substring
    partial_match = _find_partial_match(doc, vendor_quote, quote_ws_normalized, fully_normalized, force_substring_match=False)
    if partial_match:
        logger.info(f"MATCH_FOUND: TIER 5 (partial/truncated) in paragraph {partial_match.get('para_idx')}")
        return partial_match
    
    # TIER 5b: Substring matching fallback for non-truncated quotes
    # If exact match failed but quote is long or we suspect formatting differences,
    # try substring matching as a last resort
    if len(vendor_quote) > 200:  # Only for reasonably long quotes to avoid false positives
        logger.info(f"MATCH_TIER5B_START: Attempting substring fallback for quote length={len(vendor_quote)}")
        substring_match = _find_partial_match(doc, vendor_quote, quote_ws_normalized, fully_normalized, force_substring_match=True)
        if substring_match:
            logger.info(f"MATCH_FOUND: TIER 5b (substring fallback) in paragraph {substring_match.get('para_idx')}")
            return substring_match
        else:
            logger.warning(f"MATCH_TIER5B_FAILED: Substring fallback did not find match for quote length={len(vendor_quote)}")
    
    # TIER 6: Cross-paragraph matching (enhanced for long quotes)
    cross_match = _find_cross_paragraph_match(doc, vendor_quote, fully_normalized, quote_ws_normalized, fully_normalized)
    if cross_match:
        logger.info(f"MATCH_FOUND: TIER 6 (cross_paragraph) across paragraphs {cross_match.get('paragraphs', [])}")
        return cross_match
    
    logger.warning(f"MATCH_FAILED: Could not find vendor_quote in document. vendor_quote='{vendor_quote[:200]}...'")
    logger.warning(f"MATCH_FAILED: Checked {len([p for p in doc.paragraphs if p.text.strip()])} non-empty paragraphs")
    logger.warning(f"MATCH_FAILED: quote_normalized='{quote_normalized[:200]}...'")
    logger.warning(f"MATCH_FAILED: quote_ws_normalized='{quote_ws_normalized[:200]}...'")
    logger.warning(f"MATCH_FAILED: fully_normalized='{fully_normalized[:200]}...'")
    # Log character-by-character comparison for first 100 chars to debug quote issues
    if len(vendor_quote) > 0:
        logger.warning(f"MATCH_FAILED: First 100 chars of vendor_quote (hex): {vendor_quote[:100].encode('utf-8').hex()}")
        logger.warning(f"MATCH_FAILED: First 100 chars of quote_normalized (hex): {quote_normalized[:100].encode('utf-8').hex()}")
    
    # Try to find partial matches to help diagnose
    vendor_quote_lower = vendor_quote.lower()
    for para_idx, paragraph in enumerate(doc.paragraphs):
        para_text = paragraph.text
        if not para_text.strip():
            continue
        # Check if any significant portion matches
        para_lower = para_text.lower()
        # Look for a 50-character substring match
        for i in range(len(vendor_quote_lower) - 50):
            substring = vendor_quote_lower[i:i+50]
            if substring in para_lower:
                logger.warning(f"MATCH_PARTIAL: Found 50-char substring match in paragraph {para_idx}: '{substring}'")
                logger.warning(f"MATCH_PARTIAL: Paragraph {para_idx} text: '{para_text[:300]}...'")
                break
    
    # Log a sample paragraph for debugging
    sample_paras = [p.text for p in doc.paragraphs if p.text.strip()][:3]
    for i, para in enumerate(sample_paras):
        logger.warning(f"MATCH_FAILED: Sample paragraph {i} (first 200 chars): '{para[:200]}...'")
        logger.warning(f"MATCH_FAILED: Sample paragraph {i} normalized: '{normalize_for_matching(para)[:200]}...'")
    return None


def _map_normalized_to_original_position(original_text: str, normalized_pos: int) -> int:
    """
    Map a position in normalized text back to approximate position in original text.
    
    This is an approximation since whitespace normalization changes positions.
    
    Args:
        original_text: Original text before normalization
        normalized_pos: Position in normalized text
        
    Returns:
        Approximate position in original text
    """
    if normalized_pos <= 0:
        return 0
    
    # Walk through original text, counting non-collapsed characters
    normalized_count = 0
    in_whitespace = False
    
    for i, char in enumerate(original_text):
        if char in ' \t\n\r':
            if not in_whitespace:
                normalized_count += 1  # Collapsed whitespace counts as 1
                in_whitespace = True
        else:
            normalized_count += 1
            in_whitespace = False
        
        if normalized_count >= normalized_pos:
            return i + 1
    
    return len(original_text)


def _find_partial_match(doc, vendor_quote: str, quote_ws_normalized: str, fully_normalized: str, force_substring_match: bool = False) -> Optional[Dict[str, Any]]:
    """
    Find vendor_quote using partial/substring matching.
    
    If vendor quote is a prefix or substring of document text (after normalization), treat it as a match.
    This handles cases where:
    1. The LLM extracted an incomplete quote (truncated)
    2. Exact match failed but quote exists as substring (quote/formatting differences)
    3. Long quotes that span multiple paragraphs
    
    Args:
        doc: python-docx Document object
        vendor_quote: Original vendor quote text
        quote_ws_normalized: Quote and whitespace normalized vendor quote
        fully_normalized: Fully normalized vendor quote
        force_substring_match: If True, attempt substring matching even for non-truncated quotes
        
    Returns:
        Match result dict or None if not found
    """
    # Check if vendor quote appears truncated (ends mid-sentence, suspiciously short, etc.)
    is_likely_truncated = (
        len(vendor_quote) < 100 or  # Suspiciously short
        not vendor_quote.rstrip().endswith(('.', '!', '?', ';', ':', ')', ']', '}')) or  # Doesn't end with punctuation
        vendor_quote.rstrip().endswith(('or', 'and', 'the', 'a', 'an', 'to', 'of', 'in', 'for', 'with'))  # Ends with common words
    )
    
    # For long quotes (>500 chars), also try substring matching as they might span paragraphs
    is_long_quote = len(vendor_quote) > 500
    
    if not (is_likely_truncated or force_substring_match or is_long_quote):
        return None
    
    if is_likely_truncated:
        logger.info(f"MATCH_PARTIAL_CHECK: vendor_quote appears truncated (length={len(vendor_quote)}, ends_with='{vendor_quote[-20:]}')")
    elif is_long_quote:
        logger.info(f"MATCH_PARTIAL_CHECK: vendor_quote is long ({len(vendor_quote)} chars), attempting substring matching")
    elif force_substring_match:
        logger.info(f"MATCH_PARTIAL_CHECK: forcing substring match attempt (exact match failed)")
    
    # Search through paragraphs for prefix match
    for para_idx, paragraph in enumerate(doc.paragraphs):
        para_text = paragraph.text
        if not para_text.strip():
            continue
        
        # Normalize paragraph text
        para_quotes_only = normalize_quotes(para_text)
        para_quote_ws_normalized = normalize_whitespace(para_quotes_only)
        para_fully_normalized = normalize_for_matching(para_text).lower()
        
        # Check if vendor quote is a prefix of paragraph (after normalization)
        # Try multiple normalization levels
        if para_quote_ws_normalized.startswith(quote_ws_normalized):
            # Found prefix match - vendor quote is start of paragraph
            logger.info(f"MATCH_PARTIAL_FOUND: vendor_quote is prefix of paragraph {para_idx} (quote_ws_normalized)")
            start_pos = 0
            # Map normalized length back to original position
            end_pos = _map_normalized_to_original_position(para_text, len(quote_ws_normalized))
            return {
                'type': 'single_para',
                'para_idx': para_idx,
                'start_pos': start_pos,
                'end_pos': end_pos,
                'match_type': 'partial_truncated',
                'is_truncated': True
            }
        elif para_fully_normalized.startswith(fully_normalized.lower()):
            # Found prefix match with full normalization
            logger.info(f"MATCH_PARTIAL_FOUND: vendor_quote is prefix of paragraph {para_idx} (fully_normalized)")
            start_pos = 0
            end_pos = _map_normalized_to_original_position(para_text, len(fully_normalized))
            return {
                'type': 'single_para',
                'para_idx': para_idx,
                'start_pos': start_pos,
                'end_pos': end_pos,
                'match_type': 'partial_truncated',
                'is_truncated': True
            }
        
        # Check if vendor quote appears within paragraph (not just at start)
        # This handles cases where vendor quote is a substring (e.g., starts after " Conflicts. ")
        # Try multiple normalization levels - check this for ALL paragraphs, not just when not a prefix
        if quote_ws_normalized in para_quote_ws_normalized:
            norm_start = para_quote_ws_normalized.find(quote_ws_normalized)
            logger.info(f"MATCH_PARTIAL_SUBSTRING_CHECK: paragraph {para_idx}, quote_ws_normalized found at position {norm_start}")
            logger.info(f"MATCH_PARTIAL_SUBSTRING_CHECK: quote_ws_normalized length={len(quote_ws_normalized)}, para_quote_ws_normalized length={len(para_quote_ws_normalized)}")
            logger.info(f"MATCH_PARTIAL_SUBSTRING_CHECK: quote_ws_normalized preview='{quote_ws_normalized[:200]}...'")
            logger.info(f"MATCH_PARTIAL_SUBSTRING_CHECK: para_quote_ws_normalized preview='{para_quote_ws_normalized[:200]}...'")
            # Only return if it's not already a prefix match (avoid duplicate)
            if norm_start > 0:
                logger.info(f"MATCH_PARTIAL_FOUND: vendor_quote found within paragraph {para_idx} at normalized position {norm_start} (quote_ws_normalized)")
                start_pos = _map_normalized_to_original_position(para_text, norm_start)
                end_pos = _map_normalized_to_original_position(para_text, norm_start + len(quote_ws_normalized))
                return {
                    'type': 'single_para',
                    'para_idx': para_idx,
                    'start_pos': start_pos,
                    'end_pos': end_pos,
                    'match_type': 'partial_substring',
                    'is_truncated': is_likely_truncated
                }
            else:
                logger.info(f"MATCH_PARTIAL_SUBSTRING_CHECK: Skipping match at position {norm_start} (not > 0, likely prefix match)")
        elif fully_normalized.lower() in para_fully_normalized:
            norm_start = para_fully_normalized.find(fully_normalized.lower())
            logger.info(f"MATCH_PARTIAL_SUBSTRING_CHECK: paragraph {para_idx}, fully_normalized found at position {norm_start}")
            logger.info(f"MATCH_PARTIAL_SUBSTRING_CHECK: fully_normalized length={len(fully_normalized)}, para_fully_normalized length={len(para_fully_normalized)}")
            logger.info(f"MATCH_PARTIAL_SUBSTRING_CHECK: fully_normalized preview='{fully_normalized[:200]}...'")
            logger.info(f"MATCH_PARTIAL_SUBSTRING_CHECK: para_fully_normalized preview='{para_fully_normalized[:200]}...'")
            # Only return if it's not already a prefix match (avoid duplicate)
            if norm_start > 0:
                logger.info(f"MATCH_PARTIAL_FOUND: vendor_quote found within paragraph {para_idx} at normalized position {norm_start} (fully_normalized)")
                start_pos = _map_normalized_to_original_position(para_text, norm_start)
                end_pos = _map_normalized_to_original_position(para_text, norm_start + len(fully_normalized))
                return {
                    'type': 'single_para',
                    'para_idx': para_idx,
                    'start_pos': start_pos,
                    'end_pos': end_pos,
                    'match_type': 'partial_substring',
                    'is_truncated': is_likely_truncated
                }
            else:
                logger.info(f"MATCH_PARTIAL_SUBSTRING_CHECK: Skipping match at position {norm_start} (not > 0, likely prefix match)")
        else:
            # Log when substring is NOT found to help debug
            if para_idx < 100:  # Only log for first 100 paragraphs to avoid spam
                logger.info(f"MATCH_PARTIAL_SUBSTRING_CHECK: paragraph {para_idx}, substring NOT found")
                logger.info(f"MATCH_PARTIAL_SUBSTRING_CHECK: quote_ws_normalized='{quote_ws_normalized[:200]}...'")
                logger.info(f"MATCH_PARTIAL_SUBSTRING_CHECK: para_quote_ws_normalized='{para_quote_ws_normalized[:200]}...'")
                # Check if first 50 chars match (partial match indicator)
                if len(quote_ws_normalized) > 50 and quote_ws_normalized[:50] in para_quote_ws_normalized:
                    logger.warning(f"MATCH_PARTIAL_SUBSTRING_CHECK: First 50 chars of quote found in paragraph {para_idx}, but full quote not found")
                    logger.warning(f"MATCH_PARTIAL_SUBSTRING_CHECK: This suggests normalization or text differences")
    
    return None


def _find_cross_paragraph_match(doc, vendor_quote: str, normalized_quote: str, quote_ws_normalized: str = None, fully_normalized: str = None) -> Optional[Dict[str, Any]]:
    """
    Find vendor_quote that spans multiple paragraphs.
    
    Joins consecutive paragraphs and searches for the text.
    Enhanced for long quotes with better normalization handling.
    
    Args:
        doc: python-docx Document object
        vendor_quote: Original text to find
        normalized_quote: Normalized version of text
        
    Returns:
        Match result dict or None if not found
    """
    paragraphs = doc.paragraphs
    
    # Determine window size based on quote length
    # Long quotes (>500 chars) might span more paragraphs
    if len(vendor_quote) > 1000:
        window_sizes = [4, 5, 6]  # Try larger windows for very long quotes
    elif len(vendor_quote) > 500:
        window_sizes = [3, 4, 5]
    else:
        window_sizes = [2, 3]
    
    # Try joining consecutive paragraphs with different window sizes
    for window_size in window_sizes:
        for start_idx in range(len(paragraphs) - window_size + 1):
            # Join paragraphs with space
            joined_paras = []
            joined_text = ""
            for i in range(window_size):
                para_text = paragraphs[start_idx + i].text
                if para_text.strip():
                    joined_paras.append(start_idx + i)
                    joined_text += para_text + " "
            
            if not joined_text.strip():
                continue
            
            joined_text = joined_text.strip()
            
            # Try multiple normalization levels for better matching
            # First try quote+whitespace normalized if available
            if quote_ws_normalized:
                joined_quote_ws = normalize_whitespace(normalize_quotes(joined_text))
                if quote_ws_normalized in joined_quote_ws:
                    logger.info(f"CROSS_PARA_MATCH: Found (quote_ws_normalized) in paragraphs {joined_paras}")
                    return {
                        'type': 'cross_para',
                        'paragraphs': joined_paras,
                        'match_type': 'cross_paragraph_quote_ws'
                    }
            
            # Try fully normalized
            joined_normalized = normalize_for_matching(joined_text).lower()
            if normalized_quote.lower() in joined_normalized:
                logger.info(f"CROSS_PARA_MATCH: Found (fully_normalized) in paragraphs {joined_paras}")
                return {
                    'type': 'cross_para',
                    'paragraphs': joined_paras,
                    'match_type': 'cross_paragraph'
                }
            
            # Also try with fully_normalized if available
            if fully_normalized:
                if fully_normalized.lower() in joined_normalized:
                    logger.info(f"CROSS_PARA_MATCH: Found (fully_normalized) in paragraphs {joined_paras}")
                    return {
                        'type': 'cross_para',
                        'paragraphs': joined_paras,
                        'match_type': 'cross_paragraph_fully'
                    }
    
    return None


def _apply_multiple_redlines(paragraph, para_text: str, matches: List[Dict]) -> bool:
    """
    Apply multiple redlines to a single paragraph.
    
    Processes matches from end to start to preserve character positions.
    
    Args:
        paragraph: python-docx Paragraph object
        para_text: Original paragraph text
        matches: List of match dicts sorted by start_pos descending
        
    Returns:
        True if successful, False otherwise
    """
    try:
        # Build list of text segments with formatting info
        # Start with the full text, then split at each redline point
        segments = []  # List of (text, is_redline, comment)
        
        current_pos = len(para_text)
        
        for match in matches:
            start_pos = match['start_pos']
            end_pos = match['end_pos']
            comment = match['comment']
            
            # Bounds checking
            start_pos = max(0, min(start_pos, len(para_text)))
            end_pos = max(start_pos, min(end_pos, len(para_text)))
            
            # Add text after this redline (if any)
            if current_pos > end_pos:
                after_text = para_text[end_pos:current_pos]
                if after_text:
                    segments.insert(0, {'text': after_text, 'is_redline': False, 'comment': None})
            
            # Add the redlined text
            target_text = para_text[start_pos:end_pos]
            if target_text:
                segments.insert(0, {'text': target_text, 'is_redline': True, 'comment': comment})
            
            current_pos = start_pos
        
        # Add any remaining text at the beginning
        if current_pos > 0:
            before_text = para_text[:current_pos]
            if before_text:
                segments.insert(0, {'text': before_text, 'is_redline': False, 'comment': None})
        
        # Clear existing runs
        for run in paragraph.runs:
            run.clear()
        
        # Rebuild paragraph with all segments
        for segment in segments:
            run = paragraph.add_run(segment['text'])
            
            if segment['is_redline']:
                run.font.color.rgb = RGBColor(255, 0, 0)  # Red color
                run.font.strike = True  # Strikethrough
                
                # Add comment if present
                if segment['comment'] and segment['comment'].strip():
                    try:
                        run.add_comment(segment['comment'], author="One L", initials="1L")
                        logger.info(f"COMMENT_ADDED: '{segment['comment'][:50]}...' on text '{segment['text'][:30]}...'")
                    except Exception as comment_err:
                        logger.warning(f"COMMENT_FAILED: {comment_err}")
        
        logger.info(f"MULTI_REDLINE_APPLIED: {len(matches)} redlines in paragraph")
        return True
        
    except Exception as e:
        logger.error(f"Error applying multiple redlines: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        return False


def _apply_full_paragraph_redline(paragraph, comment: str):
    """
    Apply redline to entire paragraph (for cross-paragraph matches).
    
    Args:
        paragraph: python-docx Paragraph object
        comment: Comment to attach
    """
    try:
        para_text = paragraph.text
        if not para_text.strip():
            return
        
        # Clear existing runs
        for run in paragraph.runs:
            run.clear()
        
        # Add redlined text
        redline_run = paragraph.add_run(para_text)
        redline_run.font.color.rgb = RGBColor(255, 0, 0)
        redline_run.font.strike = True
        
        # Add comment (only to first paragraph in cross-para match)
        if comment and comment.strip():
            try:
                redline_run.add_comment(comment, author="One L", initials="1L")
            except Exception:
                pass
        
        logger.info(f"FULL_PARA_REDLINE: Applied to '{para_text[:50]}...'")
        
    except Exception as e:
        logger.error(f"Error applying full paragraph redline: {str(e)}")


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


def _get_google_credentials_from_secrets_manager(secret_name: str) -> dict:
    """
    Retrieve Google Cloud service account credentials from AWS Secrets Manager.
    Credentials are stored as JSON string in the secret.
    
    Args:
        secret_name: Name of the secret in AWS Secrets Manager
        
    Returns:
        Dictionary containing Google Cloud credentials, or None if not available
    """
    try:
        secrets_client = boto3.client('secretsmanager')
        response = secrets_client.get_secret_value(SecretId=secret_name)
        secret_string = response['SecretString']
        
        # Parse JSON credentials
        import json
        credentials = json.loads(secret_string)
        
        logger.info(f"Successfully retrieved Google credentials from Secrets Manager: {secret_name}")
        return credentials
    except secrets_client.exceptions.ResourceNotFoundException:
        logger.warning(f"Secret {secret_name} not found in Secrets Manager - Google Document AI will not be available")
        return None
    except Exception as e:
        logger.warning(f"Error retrieving Google credentials from Secrets Manager: {e}")
        return None


def _convert_pdf_to_docx_with_google_docai(pdf_bytes: bytes, project_id: str, processor_id: str, location: str, credentials: dict) -> Document:
    """
    Convert PDF to DOCX using Google Document AI for exact formatting preservation.
    
    Args:
        pdf_bytes: PDF file content as bytes
        project_id: Google Cloud project ID
        processor_id: Google Document AI processor ID
        location: Google Document AI location (e.g., 'us')
        credentials: Google Cloud service account credentials dictionary
        
    Returns:
        python-docx Document object
    """
    try:
        from google.cloud import documentai
        from google.oauth2 import service_account
        import json
        
        # Create credentials object from dictionary
        creds = service_account.Credentials.from_service_account_info(credentials)
        
        # Initialize Document AI client
        client = documentai.DocumentProcessorServiceClient(credentials=creds)
        
        # Construct processor name
        processor_name = client.processor_path(project_id, location, processor_id)
        
        # Process the document
        logger.info(f"Processing PDF with Google Document AI (processor: {processor_name})")
        raw_document = documentai.RawDocument(
            content=pdf_bytes,
            mime_type="application/pdf"
        )
        
        request = documentai.ProcessRequest(
            name=processor_name,
            raw_document=raw_document
        )
        
        result = client.process_document(request=request)
        document = result.document
        
        logger.info(f"Google Document AI processed {len(document.pages)} pages")
        
        # Convert Document AI output to DOCX with exact formatting
        docx_doc = Document()
        
        # Google Document AI provides structured text with layout information
        # Process pages to preserve exact formatting
        for page_idx, page in enumerate(document.pages):
            logger.info(f"Processing page {page_idx + 1} from Google Document AI")
            
            # Process paragraphs from the page
            if hasattr(page, 'paragraphs') and page.paragraphs:
                for para_idx, para_layout in enumerate(page.paragraphs):
                    if hasattr(para_layout, 'layout') and para_layout.layout:
                        # Extract text segments with formatting
                        text_anchor = para_layout.layout.text_anchor
                        if text_anchor and text_anchor.text_segments:
                            para = docx_doc.add_paragraph()
                            
                            for segment in text_anchor.text_segments:
                                start_idx = int(segment.start_index) if segment.start_index else 0
                                end_idx = int(segment.end_index) if segment.end_index else len(document.text)
                                text = document.text[start_idx:end_idx]
                                
                                if text:
                                    run = para.add_run(text)
                                    
                                    # Apply font size if available
                                    if hasattr(para_layout.layout, 'font_size'):
                                        try:
                                            font_size_value = para_layout.layout.font_size
                                            if hasattr(font_size_value, 'value'):
                                                run.font.size = Pt(float(font_size_value.value))
                                        except:
                                            pass
                                    
                                    # Apply bold if available
                                    if hasattr(para_layout.layout, 'font_weight'):
                                        if para_layout.layout.font_weight == 'bold' or para_layout.layout.font_weight == 700:
                                            run.font.bold = True
                                    
                                    # Apply italic if available
                                    if hasattr(para_layout.layout, 'font_style'):
                                        if 'italic' in para_layout.layout.font_style.lower():
                                            run.font.italic = True
            
            # Process tables if available
            if hasattr(page, 'tables') and page.tables:
                for table_layout in page.tables:
                    if hasattr(table_layout, 'header_rows') and hasattr(table_layout, 'body_rows'):
                        # Extract table data
                        table_data = []
                        # Process header rows
                        for row in table_layout.header_rows:
                            row_data = []
                            for cell in row.cells:
                                if hasattr(cell, 'layout') and cell.layout and hasattr(cell.layout, 'text_anchor'):
                                    text_anchor = cell.layout.text_anchor
                                    if text_anchor and text_anchor.text_segments:
                                        cell_text = ""
                                        for segment in text_anchor.text_segments:
                                            start_idx = int(segment.start_index) if segment.start_index else 0
                                            end_idx = int(segment.end_index) if segment.end_index else len(document.text)
                                            cell_text += document.text[start_idx:end_idx]
                                        row_data.append(cell_text)
                                    else:
                                        row_data.append("")
                                else:
                                    row_data.append("")
                            if row_data:
                                table_data.append(row_data)
                        
                        # Process body rows
                        for row in table_layout.body_rows:
                            row_data = []
                            for cell in row.cells:
                                if hasattr(cell, 'layout') and cell.layout and hasattr(cell.layout, 'text_anchor'):
                                    text_anchor = cell.layout.text_anchor
                                    if text_anchor and text_anchor.text_segments:
                                        cell_text = ""
                                        for segment in text_anchor.text_segments:
                                            start_idx = int(segment.start_index) if segment.start_index else 0
                                            end_idx = int(segment.end_index) if segment.end_index else len(document.text)
                                            cell_text += document.text[start_idx:end_idx]
                                        row_data.append(cell_text)
                                    else:
                                        row_data.append("")
                                else:
                                    row_data.append("")
                            if row_data:
                                table_data.append(row_data)
                        
                        # Create DOCX table
                        if table_data:
                            docx_table = docx_doc.add_table(rows=len(table_data), cols=len(table_data[0]) if table_data else 0)
                            docx_table.style = 'Light Grid Accent 1'
                            for row_idx, row_data in enumerate(table_data):
                                if row_idx < len(docx_table.rows):
                                    for col_idx, cell_data in enumerate(row_data):
                                        if col_idx < len(docx_table.rows[row_idx].cells):
                                            docx_table.rows[row_idx].cells[col_idx].text = str(cell_data) if cell_data else ""
        
        # Fallback: If no structured content, use full text with line breaks preserved
        if len(docx_doc.paragraphs) == 0 and document.text:
            # Split by line breaks to preserve exact structure
            lines = document.text.split('\n')
            for line in lines:
                if line.strip():
                    para = docx_doc.add_paragraph(line.strip())
        
        logger.info(f"Converted PDF to DOCX using Google Document AI - {len(docx_doc.paragraphs)} paragraphs, {len(docx_doc.tables)} tables")
        return docx_doc
        
    except Exception as e:
        logger.error(f"Google Document AI conversion failed: {e}")
        raise


def _convert_pdf_to_docx_pymupdf_fallback(pdf_bytes: bytes) -> Document:
    """
    Fallback PDF to DOCX conversion using PyMuPDF (original implementation).
    Preserves formatting, fonts, colors, images, lists, and line breaks.
    
    Args:
        pdf_bytes: PDF file content as bytes
        
    Returns:
        python-docx Document object
    """
    import fitz  # PyMuPDF
    
    docx_doc = Document()
    pdf_file = io.BytesIO(pdf_bytes)
    pdf = fitz.open(stream=pdf_file, filetype="pdf")
    
    for page_index in range(len(pdf)):
        page = pdf[page_index]
        try:
            # Extract images from page first
            try:
                image_list = page.get_images()
                if image_list:
                    logger.info(f"PDF_TO_DOCX: Found {len(image_list)} images on page {page_index + 1}")
                    for img_idx, img in enumerate(image_list):
                        try:
                            xref = img[0]
                            base_image = pdf.extract_image(xref)
                            image_bytes = base_image["image"]
                            
                            if image_bytes:
                                img_stream = io.BytesIO(image_bytes)
                                para_img = docx_doc.add_paragraph()
                                run_img = para_img.add_run()
                                run_img.add_picture(img_stream, width=Inches(6))
                                logger.info(f"PDF_TO_DOCX: Added image {img_idx + 1} to page {page_index + 1}")
                        except Exception as img_error:
                            logger.warning(f"PDF_TO_DOCX: Error extracting image {img_idx + 1}: {img_error}")
                            continue
            except Exception as img_extract_error:
                logger.debug(f"PDF_TO_DOCX: Image extraction error: {img_extract_error}")
            
            # Try to extract tables first
            try:
                tables = page.find_tables()
                if tables:
                    logger.info(f"PDF_TO_DOCX: Found {len(tables)} tables on page {page_index + 1}")
                    for table_idx, table in enumerate(tables):
                        try:
                            table_data = table.extract()
                            if table_data and len(table_data) > 0:
                                docx_table = docx_doc.add_table(rows=len(table_data), cols=len(table_data[0]) if table_data else 0)
                                docx_table.style = 'Light Grid Accent 1'
                                
                                for row_idx, row_data in enumerate(table_data):
                                    if row_idx < len(docx_table.rows):
                                        for col_idx, cell_data in enumerate(row_data):
                                            if col_idx < len(docx_table.rows[row_idx].cells):
                                                cell = docx_table.rows[row_idx].cells[col_idx]
                                                cell.text = str(cell_data) if cell_data else ""
                                logger.info(f"PDF_TO_DOCX: Added table {table_idx + 1} with {len(table_data)} rows")
                        except Exception as table_error:
                            logger.warning(f"PDF_TO_DOCX: Error extracting table {table_idx + 1}: {table_error}")
                            continue
            except (AttributeError, Exception) as table_extract_error:
                logger.debug(f"PDF_TO_DOCX: Table extraction not available: {table_extract_error}")
            
            # Extract text blocks with EXACT line-by-line preservation
            text_dict = page.get_text("dict")
            
            for block in text_dict.get("blocks", []):
                if "lines" in block:
                    for line_idx, line in enumerate(block["lines"]):
                        line_text = ""
                        spans_data = []
                        
                        for span in line.get("spans", []):
                            text = span.get("text", "")
                            if text:
                                flags = span.get("flags", 0)
                                font_size = span.get("size", 11)
                                font_color = span.get("color", 0)
                                font_name = span.get("font", "")
                                
                                line_text += text
                                spans_data.append({
                                    'text': text,
                                    'bold': bool(flags & 16),
                                    'italic': bool(flags & 2),
                                    'size': font_size,
                                    'color': font_color,
                                    'font': font_name
                                })
                        
                        if not line_text.strip():
                            continue
                        
                        # Detect numbered list patterns
                        line_text_stripped = line_text.strip()
                        is_numbered_list = False
                        list_style = None
                        
                        if re.match(r'^\d+[a-z]\b', line_text_stripped, re.IGNORECASE):
                            is_numbered_list = True
                            list_style = 'List Number 2'
                        elif re.match(r'^[\d]+[\.\)]', line_text_stripped):
                            is_numbered_list = True
                            list_style = 'List Number'
                        elif re.match(r'^[a-z][\.\)]', line_text_stripped, re.IGNORECASE):
                            is_numbered_list = True
                            list_style = 'List Bullet 2'
                        elif re.match(r'^\([a-z0-9]+\)', line_text_stripped, re.IGNORECASE):
                            is_numbered_list = True
                            list_style = 'List Bullet 2'
                        elif re.search(r'\b\d+[a-z]\b', line_text_stripped, re.IGNORECASE) and len(line_text_stripped) < 100:
                            is_numbered_list = True
                            list_style = 'List Number 2'
                        
                        if is_numbered_list:
                            para = docx_doc.add_paragraph(style=list_style)
                        else:
                            para = docx_doc.add_paragraph()
                        
                        for span_data in spans_data:
                            run = para.add_run(span_data['text'])
                            
                            if span_data['bold']:
                                run.font.bold = True
                            if span_data['italic']:
                                run.font.italic = True
                            
                            try:
                                if span_data['size'] > 0:
                                    run.font.size = Pt(span_data['size'])
                            except:
                                pass
                            
                            try:
                                color_int = span_data['color']
                                r = (color_int >> 16) & 0xFF
                                g = (color_int >> 8) & 0xFF
                                b = color_int & 0xFF
                                if not (r == 0 and g == 0 and b == 0):
                                    run.font.color.rgb = RGBColor(r, g, b)
                            except:
                                pass
                            
                            try:
                                if span_data['font']:
                                    font_map = {
                                        'Arial': 'Arial',
                                        'ArialMT': 'Arial',
                                        'Arial-BoldMT': 'Arial',
                                        'Arial-ItalicMT': 'Arial',
                                        'Helvetica': 'Arial',
                                        'Helvetica-Bold': 'Arial',
                                        'Helvetica-Oblique': 'Arial',
                                        'Times-Roman': 'Times New Roman',
                                        'TimesNewRomanPSMT': 'Times New Roman',
                                        'TimesNewRomanPS-BoldMT': 'Times New Roman',
                                        'TimesNewRomanPS-ItalicMT': 'Times New Roman',
                                        'Courier': 'Courier New',
                                        'CourierNew': 'Courier New',
                                        'CourierNewPSMT': 'Courier New',
                                        'Calibri': 'Calibri',
                                        'Calibri-Bold': 'Calibri',
                                        'Calibri-Italic': 'Calibri',
                                    }
                                    if span_data['font'] not in ['Times-Roman']:
                                        font_name = font_map.get(span_data['font'], span_data['font'])
                                        try:
                                            run.font.name = font_name
                                        except Exception as font_err:
                                            logger.debug(f"PDF_TO_DOCX: Could not set font {font_name}: {font_err}")
                                            pass
                            except Exception as font_error:
                                logger.debug(f"PDF_TO_DOCX: Font processing error: {font_error}")
                                pass
        except Exception as page_error:
            logger.warning(f"PDF_TO_DOCX: Error processing page {page_index + 1}: {page_error}")
            continue
    
    pdf.close()
    return docx_doc


def _convert_pdf_to_docx_in_processing_bucket(agent_bucket: str, pdf_s3_key: str) -> str:
    """
    Convert a PDF stored in the processing bucket into a DOCX file with EXACT formatting preservation.
    
    Uses Google Document AI for best formatting accuracy, with PyMuPDF as fallback.
    Credentials are retrieved securely from AWS Secrets Manager (no hardcoded secrets).
    
    Args:
        agent_bucket: S3 bucket name where PDF is stored
        pdf_s3_key: S3 key of the PDF file
        
    Returns:
        S3 key of the converted DOCX file
    """
    try:
        logger.info(f"PDF_TO_DOCX_START: Converting {pdf_s3_key} to DOCX with exact formatting preservation")
        
        # Download PDF from S3
        response = s3_client.get_object(Bucket=agent_bucket, Key=pdf_s3_key)
        pdf_bytes = response['Body'].read()
        logger.info(f"PDF_TO_DOCX: Downloaded PDF, size: {len(pdf_bytes)} bytes")
        
        # Try Google Document AI first (if configured) for best formatting accuracy
        docx_doc = None
        google_docai_enabled = (
            os.environ.get('GOOGLE_CLOUD_PROJECT_ID') and
            os.environ.get('GOOGLE_DOCUMENT_AI_PROCESSOR_ID') and
            os.environ.get('GOOGLE_DOCUMENT_AI_LOCATION') and
            os.environ.get('GOOGLE_CREDENTIALS_SECRET_NAME')
        )
        
        if google_docai_enabled:
            try:
                logger.info("PDF_TO_DOCX: Attempting conversion with Google Document AI for exact formatting")
                
                # Retrieve credentials from AWS Secrets Manager (secure, no hardcoded secrets)
                secret_name = os.environ.get('GOOGLE_CREDENTIALS_SECRET_NAME')
                credentials = _get_google_credentials_from_secrets_manager(secret_name)
                
                if credentials:
                    project_id = os.environ.get('GOOGLE_CLOUD_PROJECT_ID')
                    processor_id = os.environ.get('GOOGLE_DOCUMENT_AI_PROCESSOR_ID')
                    location = os.environ.get('GOOGLE_DOCUMENT_AI_LOCATION')
                    
                    docx_doc = _convert_pdf_to_docx_with_google_docai(
                        pdf_bytes, project_id, processor_id, location, credentials
                    )
                    logger.info("PDF_TO_DOCX: Successfully converted using Google Document AI")
                else:
                    logger.warning("PDF_TO_DOCX: Google credentials not available, falling back to PyMuPDF")
            except Exception as google_error:
                logger.warning(f"PDF_TO_DOCX: Google Document AI conversion failed: {google_error}, falling back to PyMuPDF")
                docx_doc = None
        
        # Fallback to PyMuPDF if Google Document AI not available or failed
        if docx_doc is None:
            logger.info("PDF_TO_DOCX: Using PyMuPDF + python-docx fallback with enhanced formatting preservation")
            try:
                docx_doc = _convert_pdf_to_docx_pymupdf_fallback(pdf_bytes)
            except Exception as pymupdf_error:
                logger.error(f"PDF_TO_DOCX_PYMUPDF_FAILED: {str(pymupdf_error)}")
                raise Exception(f"PDF to DOCX conversion failed with both Google Document AI and PyMuPDF: {str(pymupdf_error)}")
            
            # Save DOCX to memory
            docx_bytes_io = io.BytesIO()
            docx_doc.save(docx_bytes_io)
            docx_bytes_io.seek(0)
            docx_bytes = docx_bytes_io.read()
            
            # Upload DOCX to S3
            new_key = pdf_s3_key.rsplit('.', 1)[0] + '.docx'
            s3_client.put_object(
                Bucket=agent_bucket,
                Key=new_key,
                Body=docx_bytes,
                ContentType='application/vnd.openxmlformats-officedocument.wordprocessingml.document'
            )
            
        conversion_method = "Google Document AI" if google_docai_enabled and docx_doc else "PyMuPDF"
        logger.info(f"PDF_TO_DOCX_SUCCESS: Converted to {new_key} using {conversion_method} (exact formatting preserved)")
        return new_key
            
    except Exception as e:
        logger.error(f"PDF_TO_DOCX_ERROR: Failed to convert PDF to DOCX: {str(e)}")
        raise Exception(f"Failed to convert PDF to DOCX: {str(e)}")




def save_analysis_to_dynamodb(
    analysis_id: str,
    document_s3_key: str,
    analysis_data: str,
    bucket_type: str,
    usage_data: Dict[str, Any],
    thinking: str = "",
    citations: List[Dict[str, Any]] = None,
    session_id: str = None,
    user_id: str = None,
    redlined_result: Dict[str, Any] = None
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
            # Extract rationale - prefer direct field, fallback to parsing comment
            rationale = item.get('rationale', '')
            if not rationale and 'comment' in item:
                # Parse rationale from comment format: "CONFLICT ID (type): rationale\n\nReference: ..."
                comment = item['comment']
                if '): ' in comment:
                    # Extract everything after "): " and before "\n\nReference:" if present
                    rationale_part = comment.split('): ', 1)[-1]
                    if '\n\nReference:' in rationale_part:
                        rationale = rationale_part.split('\n\nReference:')[0].strip()
                    else:
                        rationale = rationale_part.strip()
                else:
                    rationale = comment
            
            conflicts.append({
                'clarification_id': item.get('clarification_id', ''),
                'vendor_conflict': item.get('text', ''),  # Exact text from vendor document (better naming)
                'summary': item.get('summary', ''),  # 20-40 word context from JSON
                'source_doc': item.get('source_doc', ''),
                'clause_ref': item.get('clause_ref', 'N/A'),
                'conflict_type': item.get('conflict_type', ''),
                'rationale': rationale or item.get('comment', '')  # Fallback to full comment if no rationale
            })
        
        # Validate and normalize JSON analysis_data before storing
        normalized_analysis_data = None
        if analysis_data:
            import json
            import re
            
            # Try to extract and validate JSON from analysis_data
            json_match = re.search(r'\[[\s\S]*\]', analysis_data)
            if json_match:
                try:
                    json_str = json_match.group(0)
                    # Validate JSON by parsing it
                    parsed_json = json.loads(json_str)
                    # Re-serialize to ensure clean, normalized JSON
                    normalized_analysis_data = json.dumps(parsed_json, indent=2, ensure_ascii=False)
                    logger.info(f"DynamoDB: Storing validated JSON with {len(parsed_json)} conflicts")
                except json.JSONDecodeError as e:
                    logger.warning(f"DynamoDB: Invalid JSON in analysis_data, storing as-is: {e}")
                    normalized_analysis_data = analysis_data
            else:
                # No JSON found, store as-is (backwards compatibility with markdown)
                normalized_analysis_data = analysis_data
                logger.info("DynamoDB: No JSON found in analysis_data, storing as text")
        
        # Prepare streamlined item for DynamoDB - focusing only on conflicts data
        item = {
            'analysis_id': analysis_id,
            'timestamp': timestamp,
            'document_s3_key': document_s3_key,
            'bucket_type': bucket_type,
            'conflicts_count': len(conflicts),
            'conflicts': conflicts
        }

        if normalized_analysis_data:
            item['analysis_data'] = normalized_analysis_data

        if usage_data:
            item['usage'] = usage_data

        if thinking:
            item['thinking'] = thinking

        if citations:
            item['citations'] = citations
        
        # Add session and user linking if provided
        if session_id:
            item['session_id'] = session_id
        if user_id:
            item['user_id'] = user_id

        if redlined_result:
            redlined_key = redlined_result.get('redlined_document')
            if redlined_key:
                item['redlined_document_s3_key'] = redlined_key
        
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

# TO DO: Change this to the new stack name dynamically instead of hardcoding it
def _get_function_names() -> Dict[str, str]:
    """Get Lambda function names based on current function naming pattern."""
    current_function = os.environ.get('AWS_LAMBDA_FUNCTION_NAME', '')
    
    if current_function and 'stepfunctions-generateredline' in current_function:
        # Extract stack name: OneL-DV2-document-review -> OneL-DV2
        stack_name = current_function.replace('-stepfunctions-generateredline', '')
        
        return {
            'delete_function': f"{stack_name}-delete-from-s3",
            'sync_function': f"{stack_name}-sync-knowledge-base"
        }
    else:
        # Fallback: use known stack name
        return {
            'delete_function': 'OneL-DV2-delete-from-s3',
            'sync_function': 'OneL-DV2-sync-knowledge-base'
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


 