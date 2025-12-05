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
                        
                        # Use Pydantic validation if available
                        if PYDANTIC_AVAILABLE and ConflictModel:
                            try:
                                validated_conflict = ConflictModel(**conflict)
                                
                                # Use comment format: CONFLICT ID (type): rationale
                                comment = f"CONFLICT {validated_conflict.clarification_id} ({validated_conflict.conflict_type}): {validated_conflict.rationale}"
                                
                                # Add reference section
                                if validated_conflict.source_doc and validated_conflict.source_doc.strip():
                                    comment += f"\n\nReference: {validated_conflict.source_doc.strip()}"
                                    if validated_conflict.clause_ref and validated_conflict.clause_ref.strip() and validated_conflict.clause_ref.lower() not in ['n/a', 'na', 'none']:
                                        comment += f" ({validated_conflict.clause_ref.strip()})"
                                
                                redline_items.append({
                                    'text': validated_conflict.vendor_quote.strip(),
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
                            
                            # Clean up vendor quote - remove surrounding quotes if present
                            vendor_quote_clean = str(vendor_quote).strip()
                            if vendor_quote_clean.startswith('"') and vendor_quote_clean.endswith('"'):
                                vendor_quote_clean = vendor_quote_clean[1:-1]
                            
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


def apply_exact_sentence_redlining(doc, redline_items: List[Dict[str, str]]) -> Dict[str, Any]:
    """
    Apply redlining to document by highlighting conflict text in red.
    Multi-tier search strategy for maximum conflict detection accuracy.
    Now processes both paragraphs and tables for comprehensive coverage.
    Enhanced with additional conflict detection strategies.
    
    Args:
        doc: python-docx Document object
        redline_items: List of conflict items with text to highlight
        
    Returns:
        Dictionary with redlining results
    """
    logger.info(f"APPLY_START: Processing {len(redline_items)} conflicts across {len(doc.paragraphs)} paragraphs and {len(doc.tables)} tables")
    try:
        matches_found = 0
        paragraphs_with_redlines = []
        tables_with_redlines = []
        total_paragraphs = len(doc.paragraphs)
        total_tables = len(doc.tables)
        failed_matches = []
        
        # Enhanced logging: Document structure analysis
        logger.info(f"DOCUMENT_STRUCTURE: Total paragraphs: {total_paragraphs}")
        logger.info(f"DOCUMENT_STRUCTURE: Total tables: {total_tables}")
        logger.info(f"DOCUMENT_STRUCTURE: Total pages estimated: {total_paragraphs // 20}")  # Rough estimate
        logger.info(f"REDLINE_ITEMS: Processing {len(redline_items)} conflicts")
        
        # ENHANCEMENT 1: Generate additional conflict variations
        enhanced_redline_items = _generate_conflict_variations(redline_items)
        logger.info(f"ENHANCEMENT: Generated {len(enhanced_redline_items)} total conflicts (including variations)")
        
        # ENHANCEMENT 2: Cross-reference matching for related concepts
        cross_reference_items = _generate_cross_reference_conflicts(enhanced_redline_items)
        logger.info(f"ENHANCEMENT: Generated {len(cross_reference_items)} cross-reference conflicts")
        
        # Combine all conflicts for processing
        all_conflicts = enhanced_redline_items + cross_reference_items
        logger.info(f"ENHANCEMENT: Processing {len(all_conflicts)} total conflicts (original + variations + cross-references)")
        

        
        # Track unmatched conflicts across tiers
        unmatched_conflicts = all_conflicts.copy()
        
        # Track already redlined paragraphs to prevent duplicates
        # Key: paragraph index, Value: set of normalized comment contents that redlined it
        already_redlined_paragraphs = {}
        already_redlined_tables = {}
        
        # Track seen comments by normalized content to prevent duplicates
        seen_comments_in_doc = set()  # Set of normalized comment contents already added to document
        
        def normalize_comment_content(comment: str) -> str:
            """Normalize comment content for duplicate detection."""
            if not comment:
                return ""
            # Remove serial numbers and extract core content
            # Pattern: "CONFLICT {number} ({type}): {rationale}"
            normalized = re.sub(r'^CONFLICT\s+\d+\s*\(', 'CONFLICT (', comment)
            normalized = normalized.strip()
            return normalized
        
        def get_comment_signature(redline_item):
            """Get normalized comment content for duplicate detection."""
            comment = redline_item.get('comment', '').strip()
            return normalize_comment_content(comment)
        
        # TIER 0: Ultra-aggressive matching for difficult cases
        remaining_conflicts = []
        logger.info(f"APPLY_TIER0: Matches: {matches_found}, Remaining: {len(remaining_conflicts)}")
        
        for redline_item in unmatched_conflicts:
            vendor_conflict_text = redline_item.get('text', '').strip()
            if not vendor_conflict_text:
                continue
                
            # Enhanced logging: Track each conflict attempt
            conflict_id = redline_item.get('id', 'Unknown')
            serial_num = redline_item.get('serial_number', 'N/A')
            source_doc = redline_item.get('source_doc', 'Unknown')
            logger.info(f"CONFLICT_ATTEMPT: Serial={serial_num}, ID={conflict_id}, Source={source_doc}, Text='{vendor_conflict_text[:100]}...'")
                
            found_match = _tier0_ultra_aggressive_matching(doc, vendor_conflict_text, redline_item)
            
            if found_match:
                para_idx = found_match['para_idx']
                comment_signature = get_comment_signature(redline_item)
                
                # Check if this comment content was already added to the document
                if comment_signature in seen_comments_in_doc:
                    logger.info(f"CONFLICT_SKIP_DUPLICATE: Comment content already added to document. Skipping duplicate.")
                    remaining_conflicts.append(redline_item)  # Try next tier
                    continue
                
                # Check if this paragraph was already redlined by the same comment content
                if para_idx in already_redlined_paragraphs:
                    if comment_signature in already_redlined_paragraphs[para_idx]:
                        logger.info(f"CONFLICT_SKIP_DUPLICATE: Paragraph {para_idx} already redlined with same comment content")
                        remaining_conflicts.append(redline_item)  # Try next tier
                        continue
                    else:
                        # Different conflict - allow it in the same paragraph
                        already_redlined_paragraphs[para_idx].add(comment_signature)
                        logger.info(f"CONFLICT_MULTIPLE: Adding conflict to paragraph {para_idx} (already has {len(already_redlined_paragraphs[para_idx])-1} other conflict(s))")
                else:
                    already_redlined_paragraphs[para_idx] = {comment_signature}
                
                # Mark this comment as seen
                seen_comments_in_doc.add(comment_signature)
                
                matches_found += 1
                if para_idx not in paragraphs_with_redlines:
                    paragraphs_with_redlines.append(para_idx)
                logger.info(f"CONFLICT_MATCHED: Serial={serial_num}, ID={conflict_id}, Paragraph={para_idx}, Page≈{para_idx // 20}")
            else:
                # Try table matching if paragraph matching failed
                table_match = _tier0_table_matching(doc, vendor_conflict_text, redline_item)
                if table_match:
                    table_idx = table_match['table_idx']
                    comment_signature = get_comment_signature(redline_item)
                    
                    # Check if this comment content was already added to the document
                    if comment_signature in seen_comments_in_doc:
                        logger.info(f"CONFLICT_SKIP_DUPLICATE: Comment content already added to document. Skipping duplicate.")
                        remaining_conflicts.append(redline_item)
                        continue
                    
                    # Check if this table was already redlined with the same comment content
                    if table_idx in already_redlined_tables:
                        if comment_signature in already_redlined_tables[table_idx]:
                            logger.info(f"CONFLICT_SKIP_DUPLICATE: Table {table_idx} already redlined with same comment content")
                            remaining_conflicts.append(redline_item)
                            continue
                        else:
                            already_redlined_tables[table_idx].add(comment_signature)
                    else:
                        already_redlined_tables[table_idx] = {comment_signature}
                    
                    # Mark this comment as seen
                    seen_comments_in_doc.add(comment_signature)
                    
                    matches_found += 1
                    if table_idx not in tables_with_redlines:
                        tables_with_redlines.append(table_idx)
                    logger.info(f"CONFLICT_MATCHED: Serial={serial_num}, ID={conflict_id}, Table={table_idx}")
                else:
                    remaining_conflicts.append(redline_item)
                    logger.info(f"CONFLICT_NO_MATCH: Serial={serial_num}, ID={conflict_id}, Text='{vendor_conflict_text[:50]}...'")
        
        # TIER 1: Standard exact matching (process remaining conflicts)
        logger.info(f"APPLY_TIER1: Matches: {matches_found}, Remaining: {len(remaining_conflicts)}")
        unmatched_conflicts = remaining_conflicts
        remaining_conflicts = []
        
        for redline_item in unmatched_conflicts:
            vendor_conflict_text = redline_item.get('text', '').strip()
            if not vendor_conflict_text:
                continue
                
            # Get base conflict ID for deduplication
            base_conflict_id = get_base_conflict_id(redline_item)

            # Enhanced logging: Track each conflict attempt
            conflict_id = redline_item.get('id', 'Unknown')
            serial_num = redline_item.get('serial_number', 'N/A')
            source_doc = redline_item.get('source_doc', 'Unknown')
            logger.info(f"CONFLICT_ATTEMPT: Serial={serial_num}, ID={conflict_id}, Source={source_doc}, Text='{vendor_conflict_text[:100]}...'")
                
            found_match = _tier1_exact_matching(doc, vendor_conflict_text, redline_item)
            
            if found_match:
                para_idx = found_match['para_idx']
                comment_signature = get_comment_signature(redline_item)
                
                # Check if this comment content was already added to the document
                if comment_signature in seen_comments_in_doc:
                    logger.info(f"CONFLICT_SKIP_DUPLICATE: Comment content already added to document. Skipping duplicate.")
                    remaining_conflicts.append(redline_item)
                    continue
                
                # Check if already redlined with same comment content
                if para_idx in already_redlined_paragraphs:
                    if comment_signature in already_redlined_paragraphs[para_idx]:
                        logger.info(f"CONFLICT_SKIP_DUPLICATE: Paragraph {para_idx} already redlined with same comment content")
                        remaining_conflicts.append(redline_item)
                        continue
                    else:
                        already_redlined_paragraphs[para_idx].add(comment_signature)
                else:
                    already_redlined_paragraphs[para_idx] = {comment_signature}
                
                # Mark this comment as seen
                seen_comments_in_doc.add(comment_signature)

                matches_found += 1
                if para_idx not in paragraphs_with_redlines:
                    paragraphs_with_redlines.append(para_idx)
                logger.info(f"CONFLICT_MATCHED: Serial={serial_num}, ID={conflict_id}, Paragraph={para_idx}, Page≈{para_idx // 20}")
            else:
                # Try semantic similarity matching before giving up
                semantic_result = _tier1_5_semantic_matching(doc, vendor_conflict_text, redline_item)
                if semantic_result:
                    para_idx = semantic_result['para_idx']
                    comment_signature = get_comment_signature(redline_item)
                    
                    # Check if this comment content was already added to the document
                    if comment_signature in seen_comments_in_doc:
                        logger.info(f"CONFLICT_SKIP_DUPLICATE: Comment content already added to document. Skipping duplicate.")
                        remaining_conflicts.append(redline_item)
                        continue
                    
                    # Check if already redlined with same comment content
                    if para_idx in already_redlined_paragraphs:
                        if comment_signature in already_redlined_paragraphs[para_idx]:
                            logger.info(f"CONFLICT_SKIP_DUPLICATE: Paragraph {para_idx} already redlined with same comment content")
                            remaining_conflicts.append(redline_item)
                            continue
                        else:
                            already_redlined_paragraphs[para_idx].add(comment_signature)
                    else:
                        already_redlined_paragraphs[para_idx] = {comment_signature}
                    
                    # Mark this comment as seen
                    seen_comments_in_doc.add(comment_signature)

                    matches_found += 1
                    if para_idx not in paragraphs_with_redlines:
                        paragraphs_with_redlines.append(para_idx)
                    logger.info(f"CONFLICT_MATCHED: Serial={serial_num}, ID={conflict_id}, Paragraph={para_idx}, Page≈{para_idx // 20}")
                else:
                    # Try table matching if paragraph matching failed
                    table_match = _tier0_table_matching(doc, vendor_conflict_text, redline_item)
                    if table_match:
                        table_idx = table_match['table_idx']
                        comment_signature = get_comment_signature(redline_item)
                        
                        # Check if this comment content was already added to the document
                        if comment_signature in seen_comments_in_doc:
                            logger.info(f"CONFLICT_SKIP_DUPLICATE: Comment content already added to document. Skipping duplicate.")
                            remaining_conflicts.append(redline_item)
                            continue
                        
                        # Check if this table was already redlined with same comment content
                        if table_idx in already_redlined_tables:
                            if comment_signature in already_redlined_tables[table_idx]:
                                logger.info(f"CONFLICT_SKIP_DUPLICATE: Table {table_idx} already redlined with same comment content")
                                remaining_conflicts.append(redline_item)
                                continue
                            else:
                                already_redlined_tables[table_idx].add(comment_signature)
                        else:
                            already_redlined_tables[table_idx] = {comment_signature}
                        
                        # Mark this comment as seen
                        seen_comments_in_doc.add(comment_signature)

                        matches_found += 1
                        if table_idx not in tables_with_redlines:
                            tables_with_redlines.append(table_idx)
                        logger.info(f"CONFLICT_MATCHED: Serial={serial_num}, ID={conflict_id}, Table={table_idx}")
                    else:
                        remaining_conflicts.append(redline_item)
                        logger.info(f"CONFLICT_NO_MATCH: Serial={serial_num}, ID={conflict_id}, Text='{vendor_conflict_text[:50]}...'")
        
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
        
        if tables_with_redlines:
            logger.info(f"TABLE_DISTRIBUTION: {len(tables_with_redlines)} tables redlined: {sorted(tables_with_redlines)}")
        
        # ENSURE EVERY PAGE HAS REDLINING - Similar to PDF coverage guarantee
        # More conservative estimate: ~15 paragraphs per page (was 20)
        estimated_pages = max(1, total_paragraphs // 15)
        pages_with_redlines_set = {}
        
        # Track how many redlines each page has
        for para_idx in paragraphs_with_redlines:
            page_num = para_idx // 15  # Use same divisor as estimation
            if page_num not in pages_with_redlines_set:
                pages_with_redlines_set[page_num] = 0
            pages_with_redlines_set[page_num] += 1
        
        # Only add review notes to pages with ZERO conflict redlines (not pages with 1 redline)
        # This reduces the number of review notes appearing in the document
        pages_needing_coverage = []
        for page_num in range(estimated_pages):
            redline_count = pages_with_redlines_set.get(page_num, 0)
            if redline_count == 0:  # Only pages with ZERO redlines get review notes
                pages_needing_coverage.append(page_num)
        
        if pages_needing_coverage:
            logger.info(f"DOCX_PAGE_COVERAGE: {len(pages_needing_coverage)} pages have zero redlines, adding single review note: {pages_needing_coverage}")
            _ensure_docx_page_coverage(doc, pages_needing_coverage, paragraphs_with_redlines, estimated_pages, min_redlines_per_page=1)
            logger.info(f"DOCX_PAGE_COVERAGE: Added review notes to {len(pages_needing_coverage)} pages with zero conflicts")
        
        # DISABLED: User requested to remove excessive review notes
        # Only add review notes to pages with ZERO conflict redlines, not all pages
        # all_pages = list(range(estimated_pages))
        # if all_pages:
        #     logger.info(f"DOCX_PAGE_COVERAGE_FINAL: Ensuring all {len(all_pages)} estimated pages have minimum coverage")
        #     _ensure_docx_page_coverage(doc, all_pages, paragraphs_with_redlines, estimated_pages, min_redlines_per_page=2)
        # Log final summary
        if failed_matches:
            logger.warning(f"REDLINING_FAILED: {len(failed_matches)} conflicts could not be matched")
            for failed in failed_matches:
                logger.warning(f"FAILED_CONFLICT: {failed.get('id', 'Unknown')} - {failed.get('text', '')[:50]}...")
        else:
            logger.info("REDLINING_SUCCESS: All conflicts successfully matched and redlined")
        
        logger.info(f"REDLINE_RESULTS: Matches found: {matches_found}")
        logger.info(f"REDLINE_RESULTS: Failed matches: {len(failed_matches)}")
        logger.info(f"REDLINE_RESULTS: Success rate: {(matches_found/len(redline_items)*100):.1f}%")
        
        return {
            "total_paragraphs": total_paragraphs,
            "total_tables": total_tables,
            "matches_found": matches_found,
            "paragraphs_with_redlines": paragraphs_with_redlines,
            "tables_with_redlines": tables_with_redlines,
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


def _ensure_docx_page_coverage(doc, pages_needing_coverage: List[int], existing_redlined_paras: List[int], estimated_pages: int, min_redlines_per_page: int = 2):
    """
    Ensure every page of a DOCX document has at least some redlining.
    More aggressive search with better page estimation.
    
    Args:
        doc: python-docx Document object
        pages_needing_coverage: List of page numbers (0-indexed) that need redlining
        existing_redlined_paras: List of paragraph indices that already have redlines
        estimated_pages: Total estimated pages in document
        min_redlines_per_page: Minimum number of redlines to ensure per page
    """
    if not pages_needing_coverage:
        return
    
    keywords = [
        'security', 'compliance', 'liability', 'indemnification', 'insurance', 'warranty',
        'confidential', 'data protection', 'privacy', 'access control', 'authentication',
        'breach', 'notification', 'termination', 'remedy', 'damages', 'sla', 'uptime',
        'availability', 'maintenance', 'backup', 'recovery', 'encryption', 'audit',
        'vendor', 'customer', 'support', 'terms', 'conditions', 'policy', 'service',
        'agreement', 'contract', 'provision', 'clause', 'requirement', 'obligation',
        'protection', 'rights', 'responsibilities', 'limitation', 'exclusion', 'warranties',
        'disclaimer', 'indemnity', 'hold harmless', 'defense', 'coverage', 'claim'
    ]
    
    for page_num in pages_needing_coverage:
        try:
            # Use more conservative paragraph estimation (~15 per page)
            para_start = page_num * 15
            para_end = min((page_num + 1) * 15, len(doc.paragraphs))
            
            if para_start >= len(doc.paragraphs):
                # Try to find any paragraph near the end
                if len(doc.paragraphs) > 0:
                    try:
                        last_para = doc.paragraphs[-1]
                        if last_para.text.strip():
                            run = last_para.add_run(" [Legal-AI: Document reviewed]")
                            run.font.color.rgb = RGBColor(128, 128, 128)
                            run.font.italic = True
                            if len(doc.paragraphs) - 1 not in existing_redlined_paras:
                                existing_redlined_paras.append(len(doc.paragraphs) - 1)
                            logger.info(f"DOCX_PAGE_COVERAGE: Added note to last paragraph for page {page_num + 1}")
                    except Exception:
                        pass
                continue
            
            # More aggressive search: check paragraphs on this page AND nearby
            search_range = range(max(0, para_start - 5), min(para_end + 10, len(doc.paragraphs)))
            redlines_added_this_page = 0
            
            # Skip keyword redlining - only add review notes if page has no conflict redlines
            # (User requested: no single-word redlining, only full conflict paragraphs)
            
            # Add ONLY ONE review note per page if page has no conflict redlines
            # This prevents excessive review notes from appearing throughout the document
            if redlines_added_this_page < min_redlines_per_page:
                for para_idx in search_range:
                    if redlines_added_this_page >= min_redlines_per_page:
                        break
                    if para_idx in existing_redlined_paras:
                        continue
                    if para_idx >= len(doc.paragraphs):
                        break
                    para = doc.paragraphs[para_idx]
                    if para.text.strip() and len(para.text.strip()) > 5:
                        try:
                            # Only add ONE review note per page (not multiple)
                            run = para.add_run(" [Legal-AI: Page reviewed for compliance]")
                            run.font.color.rgb = RGBColor(128, 128, 128)
                            run.font.italic = True
                            existing_redlined_paras.append(para_idx)
                            redlines_added_this_page += 1
                            logger.info(f"DOCX_PAGE_COVERAGE: Added single review note to para {para_idx} (page {page_num + 1})")
                            break  # Only add ONE note per page, then stop
                        except Exception as note_err:
                            logger.debug(f"DOCX_PAGE_COVERAGE: Note failed for para {para_idx}: {note_err}")
                            continue
            
            if redlines_added_this_page == 0:
                logger.warning(f"DOCX_PAGE_COVERAGE: Failed to add any redlines to page {page_num + 1}")
                            
        except Exception as page_err:
            logger.warning(f"DOCX_PAGE_COVERAGE: Error processing page {page_num + 1}: {page_err}")
            continue


def _tier0_ultra_aggressive_matching(doc, vendor_conflict_text: str, redline_item: Dict[str, str]) -> Dict[str, Any]:
    """TIER 0: Ultra-aggressive matching with sentence-level search."""
    
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
    
    def find_word_sequence_match(search_words, para_words, min_match_ratio=0.5):
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
    
    def extract_sentences_from_paragraph(para_text):
        """Extract individual sentences from a paragraph."""
        # Split on sentence boundaries but be careful with abbreviations
        sentences = re.split(r'(?<=[.!?])\s+(?=[A-Z])', para_text)
        return [s.strip() for s in sentences if s.strip()]
    
    # Ultra-normalize the search text
    ultra_normalized_search = ultra_normalize_text(vendor_conflict_text)
    search_words = extract_meaningful_words(ultra_normalized_search)
    
    logger.info(f"TIER0_SEARCH: Ultra-normalized: '{ultra_normalized_search[:100]}...'")
    logger.info(f"TIER0_WORDS: Extracted {len(search_words)} meaningful words")
    
    # Track all matches found across the ENTIRE document (not just first match)
    all_matches = []
    
    # Search through ALL paragraphs to find EVERY occurrence across ALL pages
    for para_idx, paragraph in enumerate(doc.paragraphs):
        para_text = paragraph.text.strip()
        if not para_text or len(para_text) < 10:
            continue
        
        ultra_normalized_para = ultra_normalize_text(para_text)
        para_words = extract_meaningful_words(ultra_normalized_para)
        matched = False
        
        # Check if ultra-normalized search text is in ultra-normalized paragraph
        # CRITICAL: Only pass the actual conflict text, not the entire paragraph
        if ultra_normalized_search in ultra_normalized_para:
            logger.info(f"TIER0_MATCH: Found ultra-normalized match in paragraph {para_idx}, page≈{para_idx // 20}")
            # Use the original vendor conflict text, not the entire paragraph
            _apply_redline_to_paragraph(paragraph, vendor_conflict_text, redline_item)
            all_matches.append({'para_idx': para_idx, 'matched_text': 'ultra_normalized_match'})
            matched = True
        
        # NEW: Try sentence-level matching within paragraphs
        if not matched:
            sentences = extract_sentences_from_paragraph(para_text)
            for sentence_idx, sentence in enumerate(sentences):
                ultra_normalized_sentence = ultra_normalize_text(sentence)
                sentence_words = extract_meaningful_words(ultra_normalized_sentence)
                
                # Check if search text matches this sentence
                if ultra_normalized_search in ultra_normalized_sentence:
                    logger.info(f"TIER0_SENTENCE_MATCH: Found sentence-level match in paragraph {para_idx}, sentence {sentence_idx}, page≈{para_idx // 20}")
                    # Try to find the actual conflict text within the sentence, fallback to sentence if needed
                    if vendor_conflict_text.lower() in sentence.lower():
                        _apply_redline_to_paragraph(paragraph, vendor_conflict_text, redline_item)
                    else:
                        # Use sentence as fallback, but _apply_redline_to_paragraph will try to find the conflict text
                        _apply_redline_to_paragraph(paragraph, vendor_conflict_text, redline_item)
                    all_matches.append({'para_idx': para_idx, 'matched_text': 'sentence_match'})
                    matched = True
                    break
                
                # Try word sequence matching within sentences
                if len(search_words) >= 3 and find_word_sequence_match(search_words, sentence_words):
                    logger.info(f"TIER0_SENTENCE_WORDS: Found sentence word sequence match in paragraph {para_idx}, sentence {sentence_idx}, page≈{para_idx // 20}")
                    # Use the original vendor conflict text, not the entire sentence
                    _apply_redline_to_paragraph(paragraph, vendor_conflict_text, redline_item)
                    all_matches.append({'para_idx': para_idx, 'matched_text': 'sentence_word_sequence'})
                    matched = True
                    break
        
        # Try word sequence matching at paragraph level
        if not matched and len(search_words) >= 3 and find_word_sequence_match(search_words, para_words):
            logger.info(f"TIER0_WORD_SEQUENCE: Found word sequence match in paragraph {para_idx}, page≈{para_idx // 20}")
            # Use the original vendor conflict text, not the entire paragraph
            _apply_redline_to_paragraph(paragraph, vendor_conflict_text, redline_item)
            all_matches.append({'para_idx': para_idx, 'matched_text': 'word_sequence_match'})
            matched = True
        
        # Try partial word matching (at least 70% of meaningful words match)
        if not matched and len(search_words) >= 5:
            word_matches = sum(1 for word in search_words if word.lower() in ultra_normalized_para)
            if word_matches / len(search_words) >= 0.7:
                logger.info(f"TIER0_WORD_PARTIAL: Found {word_matches}/{len(search_words)} word match in paragraph {para_idx}")
                # Use the original vendor conflict text, not the entire paragraph
                _apply_redline_to_paragraph(paragraph, vendor_conflict_text, redline_item)
                all_matches.append({'para_idx': para_idx, 'matched_text': 'word_partial_match'})
                matched = True
    
    # Return first match for compatibility, but log ALL matches found across document
    if all_matches:
        unique_paras = len(set(m['para_idx'] for m in all_matches))
        pages_affected = sorted(set(m['para_idx'] // 20 for m in all_matches))
        logger.info(f"TIER0_COMPLETE: Found {len(all_matches)} total occurrences across {unique_paras} unique paragraphs on pages {pages_affected}")
        return all_matches[0]  # Return first match for backward compatibility
    
    return None


def _tier0_table_matching(doc, vendor_conflict_text: str, redline_item: Dict[str, str]) -> Dict[str, Any]:
    """TIER 0: Ultra-aggressive table matching for conflicts in table cells."""
    
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
    
    def find_word_sequence_match(search_words, cell_words, min_match_ratio=0.5):  # Lowered from 0.6 to 0.5
        """Find if a significant portion of search words appear in sequence in cell."""
        if len(search_words) < 3:
            return False
        
        # Try to find consecutive word sequences
        for i in range(len(cell_words) - len(search_words) + 1):
            sequence = cell_words[i:i + len(search_words)]
            matches = sum(1 for j, word in enumerate(search_words) if word.lower() == sequence[j].lower())
            if matches / len(search_words) >= min_match_ratio:
                return True
        
        # Also check for shorter sequences (3-5 words) within the search words
        if len(search_words) >= 5:
            for seq_len in range(3, min(6, len(search_words))):
                for start in range(len(search_words) - seq_len + 1):
                    short_seq = search_words[start:start + seq_len]
                    for i in range(len(cell_words) - seq_len + 1):
                        cell_seq = cell_words[i:i + seq_len]
                        if all(sw.lower() == cw.lower() for sw, cw in zip(short_seq, cell_seq)):
                            return True
        
        return False
    
    def extract_key_phrases(text):
        """Extract key phrases from text that are likely to appear in documents."""
        # Remove quotes and normalize
        text_no_quotes = re.sub(r'[""''`]', '', text)
        # Extract capitalized phrases (like "Enterprise Security Office")
        capitalized_phrases = re.findall(r'\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+\b', text_no_quotes)
        # Extract noun phrases (2-4 words)
        words = text_no_quotes.split()
        noun_phrases = []
        for i in range(len(words) - 1):
            for length in [2, 3, 4]:
                if i + length <= len(words):
                    phrase = ' '.join(words[i:i+length])
                    if len(phrase) > 10:  # Meaningful length
                        noun_phrases.append(phrase)
        return capitalized_phrases[:3] + noun_phrases[:5]  # Return top phrases
    
    # Ultra-normalize the search text
    ultra_normalized_search = ultra_normalize_text(vendor_conflict_text)
    search_words = extract_meaningful_words(ultra_normalized_search)
    
    logger.info(f"TIER0_TABLE_SEARCH: Ultra-normalized: '{ultra_normalized_search[:100]}...'")
    logger.info(f"TIER0_TABLE_WORDS: Extracted {len(search_words)} meaningful words")
    
    # Track all matches found across ALL tables (not just first match)
    all_matches = []
    
    # Search through ALL tables to find EVERY occurrence
    for table_idx, table in enumerate(doc.tables):
        for row_idx, row in enumerate(table.rows):
            for cell_idx, cell in enumerate(row.cells):
                cell_text = cell.text.strip()
                if not cell_text or len(cell_text) < 10:
                    continue
                
                ultra_normalized_cell = ultra_normalize_text(cell_text)
                cell_words = extract_meaningful_words(ultra_normalized_cell)
                matched = False
                
                # Check if ultra-normalized search text is in ultra-normalized cell
                if ultra_normalized_search in ultra_normalized_cell:
                    logger.info(f"TIER0_TABLE_MATCH: Found ultra-normalized match in table {table_idx}, cell ({row_idx},{cell_idx})")
                    _apply_redline_to_table_cell(cell, cell_text, redline_item)
                    all_matches.append({'table_idx': table_idx, 'row_idx': row_idx, 'cell_idx': cell_idx, 'matched_text': 'ultra_normalized_match'})
                    matched = True
                
                # Try word sequence matching
                if not matched and len(search_words) >= 3 and find_word_sequence_match(search_words, cell_words):
                    logger.info(f"TIER0_TABLE_WORD_SEQUENCE: Found word sequence match in table {table_idx}, cell ({row_idx},{cell_idx})")
                    _apply_redline_to_table_cell(cell, cell_text, redline_item)
                    all_matches.append({'table_idx': table_idx, 'row_idx': row_idx, 'cell_idx': cell_idx, 'matched_text': 'word_sequence_match'})
                    matched = True
                
                # Try partial word matching (at least 70% of meaningful words match)
                if not matched and len(search_words) >= 5:
                    word_matches = sum(1 for word in search_words if word.lower() in ultra_normalized_cell)
                    if word_matches / len(search_words) >= 0.7:
                        logger.info(f"TIER0_TABLE_WORD_PARTIAL: Found {word_matches}/{len(search_words)} word match in table {table_idx}, cell ({row_idx},{cell_idx})")
                        _apply_redline_to_table_cell(cell, cell_text, redline_item)
                        all_matches.append({'table_idx': table_idx, 'row_idx': row_idx, 'cell_idx': cell_idx, 'matched_text': 'word_partial_match'})
                        matched = True
                
                # NEW: Try key phrase matching for quoted or technical text
                if not matched:
                    key_phrases = extract_key_phrases(vendor_conflict_text)
                    for phrase in key_phrases:
                        if len(phrase) > 15:
                            normalized_phrase = ultra_normalize_text(phrase)
                            if normalized_phrase in ultra_normalized_cell:
                                logger.info(f"TIER0_TABLE_PHRASE_MATCH: Found key phrase '{phrase[:50]}...' in table {table_idx}, cell ({row_idx},{cell_idx})")
                                _apply_redline_to_table_cell(cell, cell_text, redline_item)
                                all_matches.append({'table_idx': table_idx, 'row_idx': row_idx, 'cell_idx': cell_idx, 'matched_text': 'key_phrase_match'})
                                matched = True
                                break
    
    # Return first match for compatibility, but log all matches found
    if all_matches:
        unique_tables = len(set(m['table_idx'] for m in all_matches))
        logger.info(f"TIER0_TABLE_COMPLETE: Found {len(all_matches)} total occurrences across {unique_tables} unique tables")
        return all_matches[0]  # Return first match for backward compatibility
    
    return None


def _generate_conflict_variations(redline_items: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """Generate additional conflict variations to catch more matches - LIMITED to prevent duplicates."""
    enhanced_items = []
    
    for item in redline_items:
        # Add original item (always include)
        enhanced_items.append(item)
        
        text = item.get('text', '').strip()
        if not text or len(text) < 10:
            continue
        
        # LIMIT: Only generate 2-3 most useful variations per conflict to prevent duplicates
        variations_count = 0
        max_variations = 3
        
        # Variation 1: Remove quotes and normalize punctuation (most useful)
        normalized_text = re.sub(r'["""''`]', '"', text)
        normalized_text = re.sub(r'[–—]', '-', normalized_text)
        if normalized_text != text and variations_count < max_variations:
            enhanced_items.append({
                **item,
                'text': normalized_text,
                'variation_type': 'punctuation_normalized'
            })
            variations_count += 1
        
        # Variation 2: Remove common prefixes (only SMX and The are most common)
        prefixes_to_remove = ['SMX ', 'The ']
        for prefix in prefixes_to_remove:
            if text.startswith(prefix) and variations_count < max_variations:
                shortened_text = text[len(prefix):].strip()
                if len(shortened_text) > 30:  # Only if meaningful length remains
                    enhanced_items.append({
                        **item,
                        'text': shortened_text,
                        'variation_type': f'prefix_removed_{prefix.strip()}'
                    })
                    variations_count += 1
                    break  # Only remove first matching prefix
        
        # Variation 3: Remove parenthetical content (if it's significant)
        no_parentheses = re.sub(r'\([^)]*\)', '', text).strip()
        if no_parentheses != text and len(no_parentheses) > 30 and variations_count < max_variations:
            enhanced_items.append({
                **item,
                'text': no_parentheses,
                'variation_type': 'parentheses_removed'
            })
            variations_count += 1
    
    return enhanced_items


def _generate_cross_reference_conflicts(redline_items: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """Generate cross-reference conflicts for related concepts - DISABLED to prevent duplicates."""
    # DISABLED: This function was creating too many false positive variations
    # that matched the same paragraphs multiple times without adding value
    return []


def _tier1_5_semantic_matching(doc, vendor_conflict_text: str, redline_item: Dict[str, str]) -> Dict[str, Any]:
    """TIER 1.5: Semantic similarity matching for related concepts."""
    
    def extract_key_concepts(text):
        """Extract key concepts from text."""
        # Extract technical terms, numbers, and important phrases
        concepts = []
        
        # Technical terms
        tech_terms = re.findall(r'\b(?:SLA|API|AWS|Azure|GCP|HIPAA|FedRAMP|SOC2|ISO|NIST|RBAC|VPN|SIEM|IDS|IPS)\b', text, re.IGNORECASE)
        concepts.extend(tech_terms)
        
        # Numbers and percentages
        numbers = re.findall(r'\b\d+(?:\.\d+)?%?\b', text)
        concepts.extend(numbers)
        
        # Important phrases (3+ words)
        phrases = re.findall(r'\b\w+(?:\s+\w+){2,}\b', text)
        concepts.extend([p for p in phrases if len(p) > 10])
        
        return list(set(concepts))
    
    def calculate_concept_similarity(concepts1, concepts2):
        """Calculate similarity between two sets of concepts."""
        if not concepts1 or not concepts2:
            return 0
        
        # Convert to lowercase for comparison
        concepts1_lower = [c.lower() for c in concepts1]
        concepts2_lower = [c.lower() for c in concepts2]
        
        # Calculate Jaccard similarity
        intersection = len(set(concepts1_lower) & set(concepts2_lower))
        union = len(set(concepts1_lower) | set(concepts2_lower))
        
        return intersection / union if union > 0 else 0
    
    # Extract concepts from the conflict text
    conflict_concepts = extract_key_concepts(vendor_conflict_text)
    logger.info(f"TIER1_5_SEMANTIC: Extracted {len(conflict_concepts)} concepts from conflict text")
    
    if not conflict_concepts:
        return None
    
    best_match = None
    best_similarity = 0
    
    # Search through all paragraphs
    for para_idx, paragraph in enumerate(doc.paragraphs):
        para_text = paragraph.text.strip()
        if not para_text or len(para_text) < 20:
            continue
        
        # Extract concepts from paragraph
        para_concepts = extract_key_concepts(para_text)
        if not para_concepts:
            continue
        
        # Calculate similarity
        similarity = calculate_concept_similarity(conflict_concepts, para_concepts)
        
        if similarity > best_similarity and similarity > 0.2:  # Lowered threshold for more aggressive semantic matching
            best_similarity = similarity
            best_match = {
                'para_idx': para_idx,
                'similarity': similarity,
                'matched_concepts': list(set([c.lower() for c in conflict_concepts]) & set([c.lower() for c in para_concepts]))
            }
    
    if best_match:
        logger.info(f"TIER1_5_SEMANTIC: Found semantic match in paragraph {best_match['para_idx']} with similarity {best_match['similarity']:.3f}")
        logger.info(f"TIER1_5_SEMANTIC: Matching concepts: {best_match['matched_concepts']}")
        _apply_redline_to_paragraph(doc.paragraphs[best_match['para_idx']], para_text, redline_item)
        return best_match
    
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
    
    def extract_sentences_from_paragraph(para_text):
        """Extract individual sentences from a paragraph."""
        # Split on sentence boundaries but be careful with abbreviations
        sentences = re.split(r'(?<=[.!?])\s+(?=[A-Z])', para_text)
        return [s.strip() for s in sentences if s.strip()]
    
    # Track all matches found across the ENTIRE document (not just first match)
    all_matches = []
    
    # Search through ALL paragraphs to find EVERY occurrence across ALL pages
    for para_idx, paragraph in enumerate(doc.paragraphs):
        para_text = paragraph.text.strip()
        if not para_text:
            continue
            
        matched = False
            
        # Try each text variation for matching
        for i, text_variant in enumerate(text_variations):
            if not text_variant or matched:
                continue
                
            # Try exact substring match (most reliable with exact vendor quotes)
            if text_variant in para_text:
                logger.info(f"TIER1_MATCH: Found exact match (variation {i}) in paragraph {para_idx}, page≈{para_idx // 20}")
                _apply_redline_to_paragraph(paragraph, text_variant, redline_item)
                all_matches.append({'para_idx': para_idx, 'matched_text': text_variant})
                matched = True
                break  # Found a match with this variation, no need to try others
            
            # Try case-insensitive match as fallback
            elif text_variant.lower() in para_text.lower():
                logger.info(f"TIER1_CASE_MATCH: Found case-insensitive match (variation {i}) in paragraph {para_idx}, page≈{para_idx // 20}")
                # Find the actual text with correct case in the document
                start_idx = para_text.lower().find(text_variant.lower())
                actual_text = para_text[start_idx:start_idx + len(text_variant)]
                _apply_redline_to_paragraph(paragraph, actual_text, redline_item)
                all_matches.append({'para_idx': para_idx, 'matched_text': actual_text})
                matched = True
                break
            
            # NEW: Try sentence-level matching within paragraphs
            sentences = extract_sentences_from_paragraph(para_text)
            for sentence_idx, sentence in enumerate(sentences):
                if text_variant in sentence:
                    logger.info(f"TIER1_SENTENCE_MATCH: Found sentence-level exact match (variation {i}) in paragraph {para_idx}, sentence {sentence_idx}, page≈{para_idx // 20}")
                    _apply_redline_to_paragraph(paragraph, text_variant, redline_item)
                    all_matches.append({'para_idx': para_idx, 'matched_text': text_variant})
                    matched = True
                    break
                
                elif text_variant.lower() in sentence.lower():
                    logger.info(f"TIER1_SENTENCE_CASE_MATCH: Found sentence-level case-insensitive match (variation {i}) in paragraph {para_idx}, sentence {sentence_idx}, page≈{para_idx // 20}")
                    start_idx = sentence.lower().find(text_variant.lower())
                    actual_text = sentence[start_idx:start_idx + len(text_variant)]
                    _apply_redline_to_paragraph(paragraph, actual_text, redline_item)
                    all_matches.append({'para_idx': para_idx, 'matched_text': actual_text})
                    matched = True
                    break
            
            if matched:
                break  # Found match, no need to try more variations for this paragraph
    
    # Return first match for compatibility, but log all matches found
    if all_matches:
        unique_paras = len(set(m['para_idx'] for m in all_matches))
        pages_affected = sorted(set(m['para_idx'] // 20 for m in all_matches))
        logger.info(f"TIER1_COMPLETE: Found {len(all_matches)} total occurrences across {unique_paras} unique paragraphs on pages {pages_affected}")
        return all_matches[0]  # Return first match for backward compatibility
    
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
    
    # Track all matches found across the ENTIRE document (not just first match)
    all_matches = []
    
    # Try fuzzy matching with normalized text - search ALL paragraphs
    for para_idx, paragraph in enumerate(doc.paragraphs):
        para_text = paragraph.text.strip()
        if not para_text or len(para_text) < 10:
            continue
        
        normalized_para = enhanced_normalize_text(para_text)
        matched = False
        
        # Check if normalized search text is in normalized paragraph
        if normalized_search in normalized_para:
            logger.info(f"TIER2_MATCH: Found exact normalized match in paragraph {para_idx}, page≈{para_idx // 20}")
            _apply_redline_to_paragraph(paragraph, vendor_conflict_text[:100], redline_item)
            all_matches.append({'para_idx': para_idx, 'matched_text': 'normalized_match'})
            matched = True
        
        # Try similarity matching with lowered threshold (more aggressive)
        if not matched and len(normalized_search) > 30:
            similarity = similarity_ratio(normalized_search, normalized_para)
            if similarity > 0.65:
                logger.info(f"TIER2_SIMILARITY: Found similarity match (ratio: {similarity:.3f}) in paragraph {para_idx}, page≈{para_idx // 20}")
                _apply_redline_to_paragraph(paragraph, vendor_conflict_text[:100], redline_item)
                all_matches.append({'para_idx': para_idx, 'matched_text': 'similarity_match'})
                matched = True
        
        # Try partial phrase matching for longer texts (more aggressive)
        if not matched and len(normalized_search) > 80:
            key_phrases = extract_key_phrases(normalized_search)
            for phrase in key_phrases:
                normalized_phrase = enhanced_normalize_text(phrase)
                if normalized_phrase in normalized_para and len(normalized_phrase) > 15:
                    logger.info(f"TIER2_PHRASE: Found phrase match '{phrase[:50]}...' in paragraph {para_idx}, page≈{para_idx // 20}")
                    _apply_redline_to_paragraph(paragraph, phrase[:100], redline_item)
                    all_matches.append({'para_idx': para_idx, 'matched_text': 'phrase_match'})
                    matched = True
                    break
    
    # Return first match for compatibility, but log all matches found
    if all_matches:
        unique_paras = len(set(m['para_idx'] for m in all_matches))
        pages_affected = sorted(set(m['para_idx'] // 20 for m in all_matches))
        logger.info(f"TIER2_COMPLETE: Found {len(all_matches)} total occurrences across {unique_paras} unique paragraphs on pages {pages_affected}")
        return all_matches[0]  # Return first match for backward compatibility
    
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
    
    # Only try partial matching for longer conflict texts (lowered threshold)
    if len(vendor_conflict_text) < 60:
        return None
    
    key_phrases = extract_key_phrases(vendor_conflict_text)
    
    for para_idx, paragraph in enumerate(doc.paragraphs):
        para_text = paragraph.text.strip()
        if not para_text or len(para_text) < 30:
            continue
        
        # Check if any key phrase matches (more aggressive - lowered length threshold)
        for phrase in key_phrases:
            if len(phrase) > 15 and phrase.lower() in para_text.lower():

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
    """
    Apply redline formatting to specific text within a paragraph.
    Preserves existing formatting and allows multiple conflicts in the same paragraph.
    Only redlines the specific conflict text, not the whole paragraph.
    Prevents duplicate comments on the same text.
    """
    
    try:
        paragraph_text = paragraph.text
        
        # CRITICAL: If conflict_text is the entire paragraph, try to find the actual conflict within it
        # This prevents Tier0 from highlighting entire paragraphs
        if conflict_text.strip() == paragraph_text.strip() and len(paragraph_text) > 100:
            logger.warning(f"REDLINE_PREVENT_WHOLE_PARAGRAPH: Conflict text matches entire paragraph (length: {len(paragraph_text)}). Attempting to find specific conflict text.")
            # Try to extract a meaningful portion from the conflict item
            original_conflict = redline_item.get('text', '').strip()
            if original_conflict and len(original_conflict) < len(paragraph_text):
                conflict_text = original_conflict
                logger.info(f"REDLINE_USING_ORIGINAL: Using original conflict text (length: {len(original_conflict)}) instead of entire paragraph")
            else:
                logger.warning(f"REDLINE_SKIP: Cannot find specific conflict text in paragraph. Skipping redline to avoid highlighting entire paragraph.")
                return
        
        # Try multiple approaches to find the conflict text
        start_pos = -1
        actual_conflict_text = conflict_text
        
        # Approach 1: Exact match
        start_pos = paragraph_text.find(conflict_text)
        if start_pos != -1:
            actual_conflict_text = conflict_text
        else:
            # Approach 2: Case-insensitive match
            start_pos = paragraph_text.lower().find(conflict_text.lower())
            if start_pos != -1:
                actual_conflict_text = paragraph_text[start_pos:start_pos + len(conflict_text)]
            else:
                # Approach 3: Try with normalized text variations
                normalized_conflict = re.sub(r'[^\w\s]', ' ', conflict_text).strip()
                normalized_para = re.sub(r'[^\w\s]', ' ', paragraph_text).strip()
                
                start_pos = normalized_para.lower().find(normalized_conflict.lower())
                if start_pos != -1:
                    # Find the actual text in the original paragraph
                    # This is approximate - we'll highlight a reasonable portion
                    actual_conflict_text = conflict_text[:50] + "..." if len(conflict_text) > 50 else conflict_text
                    start_pos = paragraph_text.lower().find(actual_conflict_text.lower())
        
        # If still not found, try to find a shorter substring match
        if start_pos == -1:
            # Try finding a significant portion (at least 20 chars or 50% of conflict text)
            min_match_length = max(20, len(conflict_text) // 2)
            for match_len in range(len(conflict_text), min_match_length - 1, -1):
                substring = conflict_text[:match_len]
                start_pos = paragraph_text.lower().find(substring.lower())
                if start_pos != -1:
                    actual_conflict_text = paragraph_text[start_pos:start_pos + match_len]
                    break
        
        # Only fall back to whole paragraph if conflict text is very short or not found at all
        # and the paragraph itself is short (less than 100 chars)
        if start_pos == -1:
            if len(paragraph_text) < 100 and len(conflict_text) < 50:
                # Small paragraph, might be the whole thing
                actual_conflict_text = paragraph_text
                start_pos = 0
            else:
                logger.warning(f"Could not find conflict text '{conflict_text[:50]}...' in paragraph (length: {len(paragraph_text)}). Skipping redline.")
                return
        
        end_pos = start_pos + len(actual_conflict_text)
        
        # Check if this exact text range is already redlined with the same comment content
        # This prevents duplicate comments on the same text
        comment = redline_item.get('comment', '').strip()
        
        # Normalize comment content for duplicate detection
        def normalize_comment_for_dup_check(comment_text: str) -> str:
            """Normalize comment content for duplicate detection."""
            if not comment_text:
                return ""
            # Remove serial numbers and extract core content
            normalized = re.sub(r'^CONFLICT\s+\d+\s*\(', 'CONFLICT (', comment_text)
            normalized = normalized.strip()
            return normalized
        
        normalized_comment = normalize_comment_for_dup_check(comment)
        
        # Collect existing redlined ranges
        existing_redlined_ranges = []  # List of (start, end) tuples
        
        current_pos = 0
        for run in paragraph.runs:
            run_text = run.text
            run_start = current_pos
            run_end = current_pos + len(run_text)
            
            # Check if this run is already redlined
            is_redlined = (run.font.color and run.font.color.rgb == RGBColor(255, 0, 0)) if run.font.color and run.font.color.rgb else False
            if is_redlined:
                existing_redlined_ranges.append((run_start, run_end))
            
            current_pos = run_end
        
        # Check if this exact text range is already redlined
        # We'll check for duplicate comments separately when adding the comment
        for red_start, red_end in existing_redlined_ranges:
            if red_start == start_pos and red_end == end_pos:
                logger.info(f"REDLINE_DUPLICATE_SKIP: Exact text range {start_pos}-{end_pos} already redlined. Will check for duplicate comment when adding.")
                # Don't return here - we still want to add the comment if it's different
                break
        
        # Add new conflict range
        all_redlined_ranges = existing_redlined_ranges + [(start_pos, end_pos)]
        
        # Only merge ranges that actually overlap (not just adjacent)
        # This prevents merging separate conflicts that are close together
        if all_redlined_ranges:
            all_redlined_ranges.sort(key=lambda x: x[0])
            merged_ranges = []
            
            for current_start, current_end in all_redlined_ranges:
                merged = False
                for i, (merged_start, merged_end) in enumerate(merged_ranges):
                    # Only merge if ranges actually overlap (not just adjacent)
                    if current_start < merged_end and current_end > merged_start:
                        # Overlapping - merge the range
                        merged_ranges[i] = (min(merged_start, current_start), max(merged_end, current_end))
                        merged = True
                        break
                
                if not merged:
                    # Non-overlapping - add as new range
                    merged_ranges.append((current_start, current_end))
        else:
            merged_ranges = [(start_pos, end_pos)]
        
        # Track normalized comments already added to prevent duplicates
        # Use a module-level or function-level set to track comments across the document
        if not hasattr(_apply_redline_to_paragraph, '_seen_comments'):
            _apply_redline_to_paragraph._seen_comments = set()
        
        # Check if this normalized comment was already added
        if normalized_comment and normalized_comment in _apply_redline_to_paragraph._seen_comments:
            logger.info(f"REDLINE_COMMENT_DUPLICATE: Comment content already added to document. Skipping duplicate comment.")
            # Still highlight the text, but don't add duplicate comment
            comment = ""
        
        # Rebuild paragraph preserving existing redlines and adding new conflict
        # Clear all runs
        for run in paragraph.runs:
            run.clear()
        
        # Build paragraph with all redlined ranges marked
        current_pos = 0
        
        for red_start, red_end in merged_ranges:
            # Add text before this redlined range
            if red_start > current_pos:
                before_text = paragraph_text[current_pos:red_start]
                if before_text:
                    paragraph.add_run(before_text)
            
            # Add redlined text
            redlined_text = paragraph_text[red_start:red_end]
            # Only create run if there's actual text (prevents empty runs)
            if redlined_text.strip():
                conflict_run = paragraph.add_run(redlined_text)
                conflict_run.font.color.rgb = RGBColor(255, 0, 0)
                conflict_run.font.strike = True
                
                # Add comment only if this range is exactly our new conflict's range and comment not duplicate
                # CRITICAL: Only add comment to runs with actual text content
                if red_start == start_pos and red_end == end_pos and comment and normalized_comment and redlined_text.strip():
                    # Check if this normalized comment was already added
                    if normalized_comment not in _apply_redline_to_paragraph._seen_comments:
                        author = "One L"
                        initials = "1L"
                        conflict_run.add_comment(comment, author=author, initials=initials)
                        _apply_redline_to_paragraph._seen_comments.add(normalized_comment)
                        logger.info(f"REDLINE_COMMENT_ADDED: Added comment for conflict at {start_pos}-{end_pos} on text: '{redlined_text[:50]}...'")
                    else:
                        logger.info(f"REDLINE_COMMENT_SKIP: Comment content already exists in document. Skipping duplicate.")
            else:
                # Empty text range - skip adding run and comment
                logger.warning(f"REDLINE_SKIP_EMPTY: Skipping empty text range at {red_start}-{red_end}")
            
            current_pos = red_end
        
        # Add remaining text after last redlined range
        if current_pos < len(paragraph_text):
            after_text = paragraph_text[current_pos:]
            if after_text:
                paragraph.add_run(after_text)
            
        logger.info(f"REDLINE_APPLIED: Successfully redlined text '{actual_conflict_text[:50]}...' at position {start_pos}-{end_pos} in paragraph (preserving existing formatting)")
        
    except Exception as e:
        logger.error(f"Error applying redline to paragraph: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())


def _apply_redline_to_table_cell(cell, cell_text: str, redline_item: Dict[str, str]):
    """Apply redline formatting to specific text within a table cell."""
    
    try:
        # Process each paragraph in the cell
        for paragraph in cell.paragraphs:
            para_text = paragraph.text.strip()
            if not para_text:
                continue
                
            # Try multiple approaches to find the conflict text
            start_pos = -1
            actual_conflict_text = cell_text
            
            # Approach 1: Exact match
            start_pos = para_text.find(cell_text)
            if start_pos != -1:
                actual_conflict_text = cell_text
            else:
                # Approach 2: Case-insensitive match
                start_pos = para_text.lower().find(cell_text.lower())
                if start_pos != -1:
                    actual_conflict_text = para_text[start_pos:start_pos + len(cell_text)]
                else:
                    # Approach 3: Try with normalized text variations
                    normalized_conflict = re.sub(r'[^\w\s]', ' ', cell_text).strip()
                    normalized_para = re.sub(r'[^\w\s]', ' ', para_text).strip()
                    
                    start_pos = normalized_para.lower().find(normalized_conflict.lower())
                    if start_pos != -1:
                        # Find the actual text in the original paragraph
                        actual_conflict_text = cell_text[:50] + "..." if len(cell_text) > 50 else cell_text
                        start_pos = para_text.lower().find(actual_conflict_text.lower())
                        if start_pos == -1:
                            # Last resort: highlight the entire paragraph
                            actual_conflict_text = para_text
                            start_pos = 0
            
            # If still not found, try shorter substring match
            if start_pos == -1:
                min_match_length = max(20, len(cell_text) // 2)
                for match_len in range(len(cell_text), min_match_length - 1, -1):
                    substring = cell_text[:match_len]
                    start_pos = para_text.lower().find(substring.lower())
                    if start_pos != -1:
                        actual_conflict_text = para_text[start_pos:start_pos + match_len]
                        break
            
            if start_pos == -1:
                # Only fall back to whole paragraph if it's very short
                if len(para_text) < 100 and len(cell_text) < 50:
                    actual_conflict_text = para_text
                    start_pos = 0
                else:
                    continue  # Try next paragraph in the cell
            
            end_pos = start_pos + len(actual_conflict_text)
            
            # Collect existing redlined ranges to preserve them
            existing_redlined_ranges = []
            current_pos = 0
            for run in paragraph.runs:
                run_text = run.text
                run_start = current_pos
                run_end = current_pos + len(run_text)
                
                is_redlined = (run.font.color and run.font.color.rgb == RGBColor(255, 0, 0)) if run.font.color and run.font.color.rgb else False
                if is_redlined:
                    existing_redlined_ranges.append((run_start, run_end))
                
                current_pos = run_end
            
            # Check if this exact text range is already redlined
            for red_start, red_end in existing_redlined_ranges:
                if red_start == start_pos and red_end == end_pos:
                    logger.info(f"REDLINE_DUPLICATE_SKIP: Exact text range {start_pos}-{end_pos} already redlined in table cell. Skipping duplicate.")
                    break
            else:
                # Add new conflict range - only merge ranges that actually overlap (not just adjacent)
                all_redlined_ranges = existing_redlined_ranges + [(start_pos, end_pos)]
                if all_redlined_ranges:
                    all_redlined_ranges.sort(key=lambda x: x[0])
                    merged_ranges = []
                    for current_start, current_end in all_redlined_ranges:
                        merged = False
                        for i, (merged_start, merged_end) in enumerate(merged_ranges):
                            # Only merge if ranges actually overlap (not just adjacent)
                            if current_start < merged_end and current_end > merged_start:
                                # Overlapping - merge
                                merged_ranges[i] = (min(merged_start, current_start), max(merged_end, current_end))
                                merged = True
                                break
                        
                        if not merged:
                            # Non-overlapping - add as new range
                            merged_ranges.append((current_start, current_end))
                else:
                    merged_ranges = [(start_pos, end_pos)]
                
                # Rebuild paragraph preserving existing redlines
                for run in paragraph.runs:
                    run.clear()
                
                current_pos = 0
                for red_start, red_end in merged_ranges:
                    # Add text before this redlined range
                    if red_start > current_pos:
                        before_text = para_text[current_pos:red_start]
                        if before_text:
                            paragraph.add_run(before_text)
                    
                    # Add redlined text
                    redlined_text = para_text[red_start:red_end]
                    # Only create run if there's actual text (prevents empty runs)
                    if redlined_text.strip():
                        conflict_run = paragraph.add_run(redlined_text)
                        conflict_run.font.color.rgb = RGBColor(255, 0, 0)
                        conflict_run.font.strike = True
                        
                        # Add comment only if this range is exactly our new conflict (prevents duplicates)
                        # CRITICAL: Only add comment to runs with actual text content
                        if red_start == start_pos and red_end == end_pos and redlined_text.strip():
                            comment = redline_item.get('comment', '')
                            if comment:
                                author = "One L"
                                initials = "1L"
                                conflict_run.add_comment(comment, author=author, initials=initials)
                                logger.info(f"REDLINE_COMMENT_ADDED: Added comment for conflict at {start_pos}-{end_pos} in table cell on text: '{redlined_text[:50]}...'")
                    else:
                        # Empty text range - skip adding run and comment
                        logger.warning(f"REDLINE_SKIP_EMPTY: Skipping empty text range at {red_start}-{red_end} in table cell")
                    
                    current_pos = red_end
                
                # Add remaining text after last redlined range
                if current_pos < len(para_text):
                    after_text = para_text[current_pos:]
                    if after_text:
                        paragraph.add_run(after_text)
                
                logger.info(f"REDLINE_APPLIED: Successfully redlined text '{actual_conflict_text[:50]}...' in table cell")
                break  # Only redline the first matching paragraph in the cell
                
                current_pos = red_end
            
            # Add remaining text after last redlined range
            if current_pos < len(para_text):
                after_text = para_text[current_pos:]
                if after_text:
                    paragraph.add_run(after_text)
            
            logger.info(f"REDLINE_APPLIED: Successfully redlined text '{actual_conflict_text[:50]}...' in table cell")
            break  # Only redline the first matching paragraph in the cell
            
    except Exception as e:
        logger.error(f"Error applying redline to table cell: {str(e)}")


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


 