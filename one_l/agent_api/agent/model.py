"""
Model integration for Claude 4 Sonnet thinking with AWS Bedrock.
Handles tool calling for comprehensive document review.

Features:
- Detailed logging of LLM thinking process during all inference calls
- Thinking logs are captured and logged for:
  * Initial document review calls
  * Tool call initiation and completion
  * Chunked document analysis (per chunk)
  * All agent inference operations during redlining workflow
- Thinking content is automatically extracted from various response structures
  and logged in detail with context identifiers for easy tracking
"""

import json
import re
import boto3
from botocore.config import Config
import logging
import time
from typing import Dict, Any, List
from .system_prompt import SYSTEM_PROMPT
from .tools import retrieve_from_knowledge_base, redline_document, get_tool_definitions, save_analysis_to_dynamodb, parse_conflicts_for_redlining

# Import PDF utilities if available
try:
    from .pdf_processor import is_pdf_file
    PDF_SUPPORT_ENABLED = True
except ImportError:
    PDF_SUPPORT_ENABLED = False
    def is_pdf_file(filename): return False

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Initialize AWS clients with optimized timeout for Claude Sonnet 4 with thinking
# Optimized timeout based on typical document processing times (2-3 minutes)
# Reference: https://repost.aws/knowledge-center/bedrock-large-model-read-timeouts
bedrock_config = Config(
    read_timeout=300,  # 5 minutes - target completion within 5 minutes
)
bedrock_client = boto3.client('bedrock-runtime', config=bedrock_config)

# Model configuration - Using inference profile for Claude Sonnet 4
CLAUDE_MODEL_ID = "us.anthropic.claude-sonnet-4-20250514-v1:0"
MAX_TOKENS = 8000
TEMPERATURE = 1.0  # Must be 1.0 when thinking is enabled
THINKING_BUDGET_TOKENS = 4000

# Graceful queuing configuration for token rate limiting prevention
MAX_RETRIES = 5
BASE_DELAY = 1.0  # Base delay between calls to prevent rate limiting
MAX_DELAY = 6.0  # Max delay for exponential backoff
BACKOFF_MULTIPLIER = 2.0
CALL_SPACING_DELAY = 1.0  # Minimum delay between consecutive calls to prevent token rate limiting

# Global tracking for logging and throttling management
_call_tracker = {
    'total_tool_calls': 0,
    'total_model_calls': 0,
    'total_conflicts_detected': 0,
    'last_call_time': 0
}

def _extract_and_log_thinking(response: Dict[str, Any], context: str = "") -> str:
    """
    Extract thinking content from Claude API response and log it in detail.
    Handles various response structures from AWS Bedrock Converse API.
    
    Args:
        response: Claude API response dictionary
        context: Context string to identify where this thinking occurred (e.g., "initial_review", "chunk_1", "tool_call")
        
    Returns:
        Thinking content as string (empty if not found)
    """
    thinking_content = ""
    
    # Extract thinking from response - check multiple possible locations
    # AWS Bedrock Converse API may structure thinking in different ways
    # According to AWS docs, thinking is typically in response["output"]["thinking"] as a string
    
    # Method 1: Direct thinking field (string) - most common location
    if isinstance(response.get("thinking"), str) and response.get("thinking"):
        thinking_content = response.get("thinking", "")
        logger.info(f"Found thinking via Method 1: direct thinking field")
    
    # Method 2: Thinking in output.thinking (string) - AWS Bedrock Converse API standard location
    # Check if thinking key exists first (even if None or empty)
    elif response.get("output"):
        output = response.get("output", {})
        if "thinking" in output:
            thinking_val = output.get("thinking")
            if isinstance(thinking_val, str):
                if thinking_val and thinking_val.strip():  # Only use if not empty or whitespace
                    thinking_content = thinking_val
                    logger.info(f"Found thinking via Method 2: output.thinking (length: {len(thinking_content)})")
                else:  # Empty string means thinking was enabled but not used
                    logger.info(f"DEBUG: output.thinking exists but is empty string (thinking enabled but not used)")
            elif thinking_val is None:
                logger.info(f"DEBUG: output.thinking exists but is None")
            else:
                logger.info(f"DEBUG: output.thinking exists but is unexpected type: {type(thinking_val)}")
    
    # Method 3: Structured thinking object with content/text fields
    elif isinstance(response.get("thinking"), dict):
        thinking_obj = response.get("thinking", {})
        if isinstance(thinking_obj.get("content"), str):
            thinking_content = thinking_obj.get("content", "")
            logger.info(f"Found thinking via Method 3: thinking.content")
        elif isinstance(thinking_obj.get("text"), str):
            thinking_content = thinking_obj.get("text", "")
            logger.info(f"Found thinking via Method 3: thinking.text")
        elif isinstance(thinking_obj.get("thinking"), str):
            thinking_content = thinking_obj.get("thinking", "")
            logger.info(f"Found thinking via Method 3: thinking.thinking")
    
    # Method 4: Thinking in output.thinking as object
    elif isinstance(response.get("output", {}).get("thinking"), dict):
        thinking_obj = response.get("output", {}).get("thinking", {})
        if isinstance(thinking_obj.get("content"), str):
            thinking_content = thinking_obj.get("content", "")
            logger.info(f"Found thinking via Method 4: output.thinking.content")
        elif isinstance(thinking_obj.get("text"), str):
            thinking_content = thinking_obj.get("text", "")
            logger.info(f"Found thinking via Method 4: output.thinking.text")
        elif isinstance(thinking_obj.get("thinking"), str):
            thinking_content = thinking_obj.get("thinking", "")
            logger.info(f"Found thinking via Method 4: output.thinking.thinking")
    
    # Method 5: Check if thinking is in content blocks (some API versions)
    elif response.get("output", {}).get("message", {}).get("content"):
        for idx, content_block in enumerate(response.get("output", {}).get("message", {}).get("content", [])):
            if content_block.get("thinking"):
                if isinstance(content_block.get("thinking"), str):
                    thinking_content = content_block.get("thinking", "")
                    logger.info(f"Found thinking via Method 5: content_block[{idx}].thinking (string)")
                    break
                elif isinstance(content_block.get("thinking"), dict):
                    thinking_obj = content_block.get("thinking", {})
                    if isinstance(thinking_obj.get("content"), str):
                        thinking_content = thinking_obj.get("content", "")
                        logger.info(f"Found thinking via Method 5: content_block[{idx}].thinking.content")
                        break
                    elif isinstance(thinking_obj.get("text"), str):
                        thinking_content = thinking_obj.get("text", "")
                        logger.info(f"Found thinking via Method 5: content_block[{idx}].thinking.text")
                        break
    
    # Method 6: Check usage metadata for thinking tokens (indicates thinking was used)
    # Note: This doesn't extract thinking content, but confirms thinking was enabled
    if not thinking_content and response.get("usage"):
        usage = response.get("usage", {})
        thinking_tokens = usage.get("thinkingTokens") or usage.get("thinking_tokens") or usage.get("cachedThinkingTokens")
        if thinking_tokens:
            logger.info(f"DEBUG: Found thinking tokens in usage: {thinking_tokens} (but no thinking content found)")
    
    # Log thinking content in detail
    if thinking_content:
        thinking_length = len(thinking_content)
        thinking_preview = thinking_content[:500] if thinking_length > 500 else thinking_content
        
        logger.info(f"=== LLM THINKING [{context}] ===")
        logger.info(f"Thinking content length: {thinking_length} characters")
        logger.info(f"Thinking preview (first 500 chars):\n{thinking_preview}")
        
        # Log full thinking content (split into chunks if too long for single log entry)
        if thinking_length > 5000:
            # Split into chunks for better log readability
            chunk_size = 5000
            num_chunks = (thinking_length // chunk_size) + (1 if thinking_length % chunk_size > 0 else 0)
            logger.info(f"Thinking content is large ({thinking_length} chars), logging in {num_chunks} chunks:")
            
            for i in range(0, thinking_length, chunk_size):
                chunk_num = (i // chunk_size) + 1
                chunk_end = min(i + chunk_size, thinking_length)
                chunk = thinking_content[i:chunk_end]
                logger.info(f"--- Thinking chunk {chunk_num}/{num_chunks} [{context}] ---\n{chunk}")
        else:
            logger.info(f"--- Full thinking content [{context}] ---\n{thinking_content}")
        
        logger.info(f"=== END LLM THINKING [{context}] ===")
    else:
        # Enhanced debugging to understand response structure
        logger.warning(f"No thinking content found in response for context: {context}")
        logger.info(f"DEBUG: Response top-level keys: {list(response.keys())}")
        
        if response.get("output"):
            output = response.get("output", {})
            logger.info(f"DEBUG: Output keys: {list(output.keys())}")
            
            # Check for thinking in output
            if "thinking" in output:
                thinking_val = output.get("thinking")
                logger.info(f"DEBUG: Found 'thinking' key in output, type: {type(thinking_val)}")
                if thinking_val is None:
                    logger.info(f"DEBUG: thinking value is None")
                elif isinstance(thinking_val, str):
                    logger.info(f"DEBUG: thinking is string, length: {len(thinking_val)}, preview: {thinking_val[:200]}")
                else:
                    logger.info(f"DEBUG: thinking value preview: {str(thinking_val)[:200]}")
            
            # Check message structure
            if output.get("message"):
                message = output.get("message", {})
                logger.info(f"DEBUG: Message keys: {list(message.keys())}")
                
                # Check content blocks for thinking
                if message.get("content"):
                    content_blocks = message.get("content", [])
                    logger.info(f"DEBUG: Found {len(content_blocks)} content blocks")
                    for idx, block in enumerate(content_blocks):
                        logger.info(f"DEBUG: Content block {idx} keys: {list(block.keys())}")
                        if "thinking" in block:
                            logger.info(f"DEBUG: Found 'thinking' in content block {idx}, type: {type(block.get('thinking'))}")
        
        # Check if thinking is at top level
        if "thinking" in response:
            thinking_val = response.get("thinking")
            logger.info(f"DEBUG: Found 'thinking' key at top level, type: {type(thinking_val)}, value preview: {str(thinking_val)[:200] if thinking_val else 'None'}")
    
    return thinking_content if thinking_content else ""

def _extract_json_only(content: str) -> str:
    """
    Extract only JSON array from response content, stripping any explanatory text.
    This ensures we only return valid JSON even if the agent adds explanatory text.
    
    Args:
        content: Raw response content that may contain JSON plus explanatory text
        
    Returns:
        Clean JSON string (array) or empty array if no valid JSON found
    """
    if not content:
        return "[]"
    
    # First, try to find JSON array pattern (most common case)
    json_match = re.search(r'\[[\s\S]*?\]', content)
    if json_match:
        json_str = json_match.group(0)
        # Validate it's actually valid JSON
        try:
            json.loads(json_str)
            logger.info(f"Extracted valid JSON array from response (length: {len(json_str)} chars)")
            return json_str
        except json.JSONDecodeError:
            logger.warning("Found array-like pattern but not valid JSON, trying to fix...")
    
    # If no array pattern found, try to find any JSON object/array
    # Look for content starting with [ or {
    content_trimmed = content.strip()
    
    # Try to find JSON starting from the first [
    start_idx = content_trimmed.find('[')
    if start_idx != -1:
        # Try to parse from [ to end, or find matching ]
        bracket_count = 0
        end_idx = start_idx
        for i in range(start_idx, len(content_trimmed)):
            if content_trimmed[i] == '[':
                bracket_count += 1
            elif content_trimmed[i] == ']':
                bracket_count -= 1
                if bracket_count == 0:
                    end_idx = i + 1
                    break
        
        if bracket_count == 0 and end_idx > start_idx:
            json_str = content_trimmed[start_idx:end_idx]
            try:
                json.loads(json_str)
                logger.info(f"Extracted JSON array by bracket matching (length: {len(json_str)} chars)")
                return json_str
            except json.JSONDecodeError:
                pass
    
    # If all else fails, log warning and return empty array
    logger.warning(f"Could not extract valid JSON from response. Response preview: {content[:200]}...")
    return "[]"

def _split_document_into_chunks(doc, chunk_size=100, overlap=5):
    """
    Split a document into chunks for better processing.
    Uses simple paragraph slicing - each chunk becomes its own smaller document.
    
    Args:
        doc: python-docx Document object
        chunk_size: Number of paragraphs per chunk (~5 pages)
        overlap: Number of paragraphs to overlap between chunks
        
    Returns:
        List of (start_idx, end_idx, chunk_doc) tuples
    """
    from docx import Document
    from docx.oxml import OxmlElement
    import io
    
    chunks = []
    total_paragraphs = len(doc.paragraphs)
    start_idx = 0
    chunk_num = 0
    
    while start_idx < total_paragraphs:
        end_idx = min(start_idx + chunk_size, total_paragraphs)
        
        # Create a new document for this chunk
        chunk_doc = Document()
        
        # Copy paragraphs with their content
        for i in range(start_idx, end_idx):
            src_para = doc.paragraphs[i]
            new_para = chunk_doc.add_paragraph()
            
            # Copy the paragraph text
            para_text = src_para.text
            if para_text.strip():  # Only add non-empty paragraphs
                new_para.add_run(para_text)
        
        # Save to bytes
        buffer = io.BytesIO()
        chunk_doc.save(buffer)
        chunk_bytes = buffer.getvalue()
        
        chunks.append({
            'bytes': chunk_bytes,
            'start_para': start_idx,
            'end_para': end_idx,
            'num_paragraphs': end_idx - start_idx,
            'chunk_num': chunk_num
        })
        
        chunk_num += 1
        start_idx += chunk_size - overlap
        
        if start_idx >= total_paragraphs - overlap:
            break
    
    return chunks


class Model:
    """
    Handles document review using Claude 4 Sonnet thinking with tool calling.
    """
    
    def __init__(self, knowledge_base_id: str, region: str):
        self.knowledge_base_id = knowledge_base_id
        self.region = region
        self.tools = get_tool_definitions()
    

    
    def review_document(self, bucket_type: str, document_s3_key: str) -> Dict[str, Any]:
        """
        Review a vendor document for conflicts using Claude 4 Sonnet thinking with tools.
        
        Args:
            bucket_type: Type of source bucket
            document_s3_key: S3 key of the document to review
            
        Returns:
            Dictionary containing the review results
        """
        
        try:
            logger.info(f"Starting document review with Claude 4 Sonnet thinking and tools")
            
            # Get document from S3 for attachment
            bucket_name = self._get_bucket_name(bucket_type)
            s3_client = boto3.client('s3')
            
            response = s3_client.get_object(Bucket=bucket_name, Key=document_s3_key)
            document_data = response['Body'].read()
            
            # Prepare the conversation with document attachment
            import base64
            import os
            
            # Extract and sanitize filename for document name
            filename = self._sanitize_filename_for_converse(os.path.basename(document_s3_key))
            
            # Check document size and decide whether to chunk
            # Only try to parse DOCX files for chunking (PDFs are handled differently)
            instruction_text = "Please analyze this vendor submission document completely, including all pages and sections. After identifying all conflicts, return the output in the expected JSON array format."
            
            if not is_pdf_file(document_s3_key):
                try:
                    from docx import Document
                    import io
                    doc = Document(io.BytesIO(document_data))
                    total_paragraphs = len(doc.paragraphs)
                    logger.info(f"Document has {total_paragraphs} paragraphs ")
                    
                    # If document is very large (>100 paragraphs ≈ 5+ pages), split into chunks
                    if total_paragraphs > 100:
                        logger.warning(f"Large document detected ({total_paragraphs} paragraphs). Splitting into chunks for comprehensive analysis.")
                        return self._review_document_chunked(doc, document_data, document_s3_key, bucket_type, filename)
                    else:
                        instruction_text = "Please analyze this vendor submission document completely, including all pages and sections. After identifying all conflicts, return the output in the expected JSON array format."
                except Exception as e:
                    logger.warning(f"Could not pre-analyze document structure: {e}")
            else:
                logger.info(f"PDF document detected: {document_s3_key} - skipping DOCX pre-analysis")
            
            messages = [
                {
                    "role": "user",
                    "content": [
                        {
                            "text": instruction_text
                        },
                        {
                            "document": {
                                "format": self._get_document_format(document_s3_key),
                                "name": filename,
                                "source": {
                                    "bytes": document_data
                                }
                            }
                        }
                    ]
                }
            ]
            
            # Make the initial request to Claude with tools
            logger.info("=== STARTING DOCUMENT REVIEW INFERENCE ===")
            response = self._call_claude_with_tools(messages)
            
            # Log thinking from document review response
            logger.info("=== LOGGING THINKING FROM DOCUMENT REVIEW ===")
            thinking_content = _extract_and_log_thinking(response, f"document_review_{document_s3_key}")
            
            # Extract content from Converse API response
            content = ""
            if response.get("output", {}).get("message", {}).get("content"):
                for content_block in response["output"]["message"]["content"]:
                    if content_block.get("text"):
                        content += content_block["text"]
            
            # Extract only JSON from response, stripping any explanatory text
            content = _extract_json_only(content)
            
            # Count conflicts detected in the analysis
            try:
                conflicts = parse_conflicts_for_redlining(content)
                conflicts_count = len(conflicts)
                _call_tracker['total_conflicts_detected'] += conflicts_count
            except Exception as e:
                logger.warning(f"Error counting conflicts: {str(e)}")
                conflicts_count = 0
            
            # Log final summary with all metrics
            logger.info(f"DOCUMENT REVIEW COMPLETE - Total Tool Calls: {_call_tracker['total_tool_calls']}, Total Model Calls: {_call_tracker['total_model_calls']}, Total Conflicts Detected: {_call_tracker['total_conflicts_detected']} (this document: {conflicts_count})")
            
            # Extract thinking for return value
            thinking_for_return = _extract_and_log_thinking(response, f"final_review_{document_s3_key}")
            if not thinking_for_return:
                thinking_for_return = response.get("thinking", "")
            
            return {
                "success": True,
                "analysis": content,
                "tool_results": response.get("tool_results", []),
                "usage": response.get("usage", {}),
                "thinking": thinking_for_return,
                "conflicts_count": conflicts_count
            }
            
        except Exception as e:
            logger.error(f"Error in document review: {str(e)}")
            return {
                "success": False,
                "error": str(e),
                "analysis": "",
                "tool_results": [],
                "usage": {},
                "thinking": ""
            }
    
    def _review_document_chunked(self, doc, document_data, document_s3_key: str, bucket_type: str, filename: str) -> Dict[str, Any]:
        """
        Review large documents by splitting into chunks and analyzing each separately.
        This ensures Claude can see ALL pages of large documents.
        
        Args:
            doc: python-docx Document object
            document_data: Raw document bytes
            document_s3_key: S3 key of the document
            bucket_type: Type of source bucket
            filename: Sanitized filename
            
        Returns:
            Dictionary containing merged review results from all chunks
        """
        try:
            logger.info(f"Starting chunked document review")
            
            # Split document into chunks
            chunks = _split_document_into_chunks(doc, chunk_size=100, overlap=5)
            logger.info(f"Split document into {len(chunks)} chunks for analysis")
            
            all_content = []
            all_tool_results = []
            all_conflicts = []
            total_tokens_used = 0
            
            # Track Additional-[#] counter across chunks to ensure sequential numbering
            additional_counter = 0
            
            # Process each chunk
            for chunk_info in chunks:
                chunk_num = chunk_info['chunk_num']
                start_para = chunk_info['start_para']
                end_para = chunk_info['end_para']
                chunk_bytes = chunk_info['bytes']
                
                logger.info(f"Analyzing chunk {chunk_num + 1}/{len(chunks)} (paragraphs {start_para}-{end_para})")
                
                # Create instruction for this specific chunk with Additional counter context
                approx_pages = f"(approximately pages {start_para//15 + 1}-{end_para//15 + 1})"
                additional_context = ""
                if chunk_num > 0:  # Not the first chunk
                    additional_context = f" IMPORTANT: For conflicts that don't have a vendor-provided ID, use 'Additional-{additional_counter + 1}', 'Additional-{additional_counter + 2}', etc. (continuing from previous sections)."
                
                instruction_text = f"Analyze this vendor submission section {approx_pages} for MATERIAL conflicts with Massachusetts Commonwealth requirements. Focus on issues that have real business or legal impact - changes to obligations, risk allocation, financial terms, service delivery, or compliance requirements. Look for substantive differences that create actual risk or modify important rights. Do NOT flag minor language differences that don't change meaning. For each conflict you find, explain the practical business impact in the rationale field - what risk it creates and why it matters.{additional_context} Output ONLY a JSON array of conflicts (empty array [] if no conflicts found). Do not include any explanatory text or markdown formatting."
                
                messages = [
                    {
                        "role": "user",
                        "content": [
                            {
                                "text": instruction_text
                            },
                            {
                                "document": {
                                    "format": self._get_document_format(document_s3_key),
                                    "name": f"{filename}_chunk_{chunk_num}",
                                    "source": {
                                        "bytes": chunk_bytes
                                    }
                                }
                            }
                        ]
                    }
                ]
                
                # Analyze this chunk
                logger.info(f"=== STARTING CHUNK {chunk_num + 1} INFERENCE ===")
                response = self._call_claude_with_tools(messages)
                
                # Log thinking from chunk analysis response
                logger.info(f"=== LOGGING THINKING FROM CHUNK {chunk_num + 1} ANALYSIS ===")
                thinking_content = _extract_and_log_thinking(response, f"chunk_{chunk_num + 1}_of_{len(chunks)}")
                
                # Extract content
                content = ""
                if response.get("output", {}).get("message", {}).get("content"):
                    for content_block in response["output"]["message"]["content"]:
                        if content_block.get("text"):
                            content += content_block["text"]
                
                # Extract only JSON from response, stripping any explanatory text
                content = _extract_json_only(content)
                
                all_content.append(content)
                all_tool_results.append(response.get("tool_results", []))
                
                # Track usage
                if response.get("usage"):
                    total_tokens_used += response.get("usage", {}).get("totalTokens", 0)
                
                # Count conflicts in this chunk and update Additional counter
                try:
                    conflicts = parse_conflicts_for_redlining(content)
                    all_conflicts.extend(conflicts)
                    
                    # Count Additional-[#] conflicts in this chunk to update counter for next chunk
                    for conflict in conflicts:
                        clarification_id = conflict.get('clarification_id', '')
                        if isinstance(clarification_id, str) and clarification_id.startswith('Additional-'):
                            try:
                                # Extract number from "Additional-1", "Additional-2", etc.
                                additional_num = int(clarification_id.split('-')[1])
                                additional_counter = max(additional_counter, additional_num)
                            except (ValueError, IndexError):
                                # If parsing fails, just increment counter
                                additional_counter += 1
                    
                    logger.info(f"Found {len(conflicts)} conflicts in chunk {chunk_num + 1}, Additional counter now at {additional_counter}")
                except Exception as e:
                    logger.warning(f"Error parsing conflicts from chunk {chunk_num + 1}: {str(e)}")
            
            # Merge all content - combine JSON arrays from all chunks into a single valid JSON array
            import json
            import re
            
            merged_json_conflicts = []
            
            # Try to extract and combine JSON arrays from each chunk
            # Also renumber Additional-[#] conflicts to ensure sequential numbering across chunks
            chunks_with_json = 0
            global_additional_counter = 0  # Track Additional counter across all chunks for renumbering
            
            for chunk_idx, chunk_content in enumerate(all_content):
                # Look for JSON array pattern in the chunk content
                json_match = re.search(r'\[[\s\S]*\]', chunk_content)
                if json_match:
                    try:
                        json_str = json_match.group(0)
                        chunk_conflicts = json.loads(json_str)
                        if isinstance(chunk_conflicts, list):
                            # Handle empty arrays (valid JSON response when no conflicts)
                            if len(chunk_conflicts) == 0:
                                logger.info(f"Chunk {chunk_idx + 1} returned empty JSON array (no conflicts found)")
                            else:
                                # Renumber Additional-[#] conflicts to ensure sequential numbering
                                for conflict in chunk_conflicts:
                                    if isinstance(conflict, dict):
                                        clarification_id = conflict.get('clarification_id', '')
                                        if isinstance(clarification_id, str) and clarification_id.startswith('Additional-'):
                                            global_additional_counter += 1
                                            conflict['clarification_id'] = f'Additional-{global_additional_counter}'
                                            logger.debug(f"Renumbered Additional conflict to Additional-{global_additional_counter}")
                                
                                merged_json_conflicts.extend(chunk_conflicts)
                                logger.info(f"Merged {len(chunk_conflicts)} conflicts from chunk {chunk_idx + 1} JSON (Additional counter: {global_additional_counter})")
                            chunks_with_json += 1
                    except json.JSONDecodeError as e:
                        logger.warning(f"Failed to parse JSON from chunk {chunk_idx + 1}, falling back to text merge: {e}")
            
            # If we successfully found and merged JSON arrays, create a single valid JSON string
            if chunks_with_json > 0:
                # Always output valid JSON array, even if empty
                merged_content = json.dumps(merged_json_conflicts, indent=2, ensure_ascii=False)
                logger.info(f"Successfully merged {len(merged_json_conflicts)} total conflicts from {chunks_with_json} chunks into single JSON array")
            else:
                # Fallback: if no JSON found, merge as text (backwards compatibility)
                merged_content = "\n\n--- ANALYSIS CONTINUED FROM NEXT SECTION ---\n\n".join(all_content)
                logger.warning("No JSON arrays found in chunks, using text merge fallback")
            
            # Log final summary
            total_conflicts = len(all_conflicts)
            _call_tracker['total_conflicts_detected'] += total_conflicts
            
            logger.info(f"CHUNKED DOCUMENT REVIEW COMPLETE - Total Chunks: {len(chunks)}, Total Conflicts: {total_conflicts}, Total Tokens Used: {total_tokens_used}")
            
            # Collect all thinking from chunks for return (if available)
            # Note: Individual chunk thinking was already logged above
            logger.info("=== CHUNKED REVIEW SUMMARY: All chunk thinking has been logged above ===")
            
            return {
                "success": True,
                "analysis": merged_content,
                "tool_results": all_tool_results,
                "usage": {"totalTokens": total_tokens_used},
                "thinking": "",  # Chunked reviews have thinking logged per chunk above
                "conflicts_count": total_conflicts
            }
            
        except Exception as e:
            logger.error(f"Error in chunked document review: {str(e)}")
            return {
                "success": False,
                "error": str(e),
                "analysis": "",
                "tool_results": [],
                "usage": {},
                "thinking": ""
            }
    
    def _get_bucket_name(self, bucket_type: str) -> str:
        """Get the appropriate bucket name based on type."""
        import os
        if bucket_type == "knowledge":
            return os.environ.get("KNOWLEDGE_BUCKET")
        elif bucket_type == "user_documents":
            return os.environ.get("USER_DOCUMENTS_BUCKET")
        elif bucket_type == "agent_processing":
            return os.environ.get("AGENT_PROCESSING_BUCKET")
        else:
            raise ValueError(f"Invalid bucket_type: {bucket_type}")
    
    def _get_document_format(self, document_s3_key: str) -> str:
        """Determine document format based on file extension for Converse API."""
        file_extension = document_s3_key.lower().split('.')[-1]
        
        # Converse API supported formats
        supported_formats = ['pdf', 'csv', 'doc', 'docx', 'xls', 'xlsx', 'html', 'txt', 'md']
        
        if file_extension in supported_formats:
            return file_extension
        else:
            # Default to PDF if extension not recognized
            logger.warning(f"Unknown file extension '{file_extension}', defaulting to PDF format")
            return 'pdf'
    
    def _sanitize_filename_for_converse(self, filename: str) -> str:
        """
        Sanitize filename to meet Converse API requirements:
        - Only alphanumeric, whitespace, hyphens, parentheses, square brackets
        - No more than one consecutive whitespace character
        """
        import re
        
        # If filename contains UUID or timestamp patterns, extract just the original name
        # Pattern: uuid_originalname.ext or timestamp/uuid_originalname.ext
        if '_' in filename:
            # Try to extract original filename after last underscore
            parts = filename.split('_')
            if len(parts) > 1:
                # Keep everything after the last underscore (likely the original filename)
                filename = parts[-1]
        
        # Remove any invalid characters - keep only allowed ones
        # Allowed: alphanumeric, whitespace, hyphens, parentheses, square brackets
        sanitized = re.sub(r'[^a-zA-Z0-9\s\-\(\)\[\]]', '', filename)
        
        # Replace multiple consecutive whitespace with single space
        sanitized = re.sub(r'\s+', ' ', sanitized)
        
        # Trim whitespace from start/end
        sanitized = sanitized.strip()
        
        # If sanitization left us with empty string, use default
        if not sanitized:
            sanitized = "vendor-submission"
        
        logger.info(f"Sanitized filename from '{filename}' to '{sanitized}'")
        return sanitized
    
    def _call_claude_with_tools(self, messages: List[Dict[str, Any]], retry_count: int = 0) -> Dict[str, Any]:
        """
        Call Claude 4 Sonnet thinking with tool support using Converse API.
        Implements graceful queuing with call spacing to prevent token rate limiting.
        """
        
        # Implement graceful call spacing to prevent token rate limiting
        current_time = time.time()
        time_since_last_call = current_time - _call_tracker['last_call_time']
        
        if time_since_last_call < CALL_SPACING_DELAY:
            wait_time = CALL_SPACING_DELAY - time_since_last_call
            logger.info(f"Graceful queuing: waiting {wait_time:.2f}s to prevent token rate limiting")
            time.sleep(wait_time)
        
        # Log attempt (but don't increment counter until successful)
        logger.info(f"Calling Claude with {len(messages)} messages and {len(self.tools)} tools (attempt {retry_count + 1}) - Total successful calls so far: {_call_tracker['total_model_calls']}")
        
        try:
            # Call Bedrock using Converse API (supports document attachments)
            response = bedrock_client.converse(
                modelId=CLAUDE_MODEL_ID,
                messages=messages,
                system=[{"text": SYSTEM_PROMPT}],
                inferenceConfig={
                    "maxTokens": MAX_TOKENS,
                    "temperature": TEMPERATURE
                },
                toolConfig={"tools": self.tools},
                additionalModelRequestFields={
                    "thinking": {
                        "type": "enabled",
                        "budget_tokens": THINKING_BUDGET_TOKENS
                    }
                }
            )
            
            # SUCCESS: Only now increment the counter for successful calls
            _call_tracker['total_model_calls'] += 1
            _call_tracker['last_call_time'] = time.time()
            
            logger.info(f"Claude API call successful! Total successful model calls: {_call_tracker['total_model_calls']}")
            
            # Log full response structure for debugging (first time only to avoid log spam)
            if _call_tracker['total_model_calls'] == 1:
                logger.info(f"DEBUG: Full response structure - Top-level keys: {list(response.keys())}")
                logger.info(f"DEBUG: Response structure preview: {json.dumps({k: str(type(v).__name__) + (' (len=' + str(len(v)) + ')' if isinstance(v, (list, dict, str)) else '') for k, v in response.items()}, indent=2)}")
            
            # Extract and log thinking content
            thinking_context = f"inference_call_{_call_tracker['total_model_calls']}"
            if response.get("stopReason") == "tool_use":
                thinking_context += "_before_tool_use"
            _extract_and_log_thinking(response, thinking_context)
            
            # Handle tool calls if present
            if response.get("stopReason") == "tool_use":
                return self._handle_tool_calls(messages, response)
            
            return response
            
        except Exception as e:
            error_msg = str(e)
            
            # Check for throttling errors and implement exponential backoff
            if ("ThrottlingException" in error_msg or "Too many tokens" in error_msg or 
                "rate" in error_msg.lower() or "throttl" in error_msg.lower() or
                "limit" in error_msg.lower()):
                
                if retry_count < MAX_RETRIES:
                    delay = min(BASE_DELAY * (BACKOFF_MULTIPLIER ** retry_count), MAX_DELAY)
                    logger.warning(f"Claude API throttling detected, retrying in {delay} seconds (attempt {retry_count + 1}/{MAX_RETRIES + 1})")
                    time.sleep(delay)
                    return self._call_claude_with_tools(messages, retry_count + 1)
                else:
                    logger.error(f"Max retries exceeded for Claude API throttling after {MAX_RETRIES + 1} attempts")
                    raise Exception(f"Claude API throttling limit exceeded after {MAX_RETRIES + 1} retries. Please try again later when token limits reset.")
            else:
                # Non-throttling error - don't retry
                logger.error(f"Error calling Claude (non-throttling): {str(e)}")
                raise
    
    def _handle_tool_calls(self, messages: List[Dict[str, Any]], claude_response: Dict[str, Any]) -> Dict[str, Any]:
        """
        Handle tool calls from Claude and continue the conversation.
        """
        
        # Log thinking from the initial tool use response
        logger.info("=== TOOL CALL DETECTED - Logging thinking before tool execution ===")
        _extract_and_log_thinking(claude_response, f"tool_call_initiation_{_call_tracker['total_tool_calls'] + 1}")
        
        # Add Claude's response to messages (Converse API format)
        messages.append({
            "role": "assistant",
            "content": claude_response["output"]["message"]["content"]
        })
        
        tool_results = []
        
        # Process each tool call (Converse API format)
        for content_block in claude_response["output"]["message"]["content"]:
            if content_block.get("toolUse"):
                tool_use = content_block["toolUse"]
                tool_name = tool_use["name"]
                tool_input = tool_use["input"]
                tool_use_id = tool_use["toolUseId"]
                
                # Update tool call tracking (safe to increment here - no retries for tools)
                _call_tracker['total_tool_calls'] += 1
                
                logger.info(f"Executing tool: {tool_name} with input: {tool_input} - Total tool calls: {_call_tracker['total_tool_calls']}")
                
                # Execute the tool using existing tools.py functions
                try:
                    if tool_name == "retrieve_from_knowledge_base":
                        result = retrieve_from_knowledge_base(
                            query=tool_input["query"],
                            max_results=tool_input.get("max_results", 100),
                            knowledge_base_id=self.knowledge_base_id,
                            region=self.region
                        )
                        logger.info(f"Tool {tool_name} executed successfully")
                    else:
                        result = {"error": f"Unknown tool: {tool_name}"}
                        logger.warning(f"Unknown tool requested: {tool_name}")
                    
                    tool_results.append({
                        "tool_name": tool_name,
                        "input": tool_input,
                        "result": result
                    })
                    
                    # Add tool result to messages (Converse API format)
                    messages.append({
                        "role": "user",
                        "content": [
                            {
                                "toolResult": {
                                    "toolUseId": tool_use_id,
                                    "content": [{"json": result}]
                                }
                            }
                        ]
                    })
                    
                except Exception as e:
                    logger.error(f"Error executing tool {tool_name}: {str(e)}")
                    error_result = {"error": str(e)}
                    tool_results.append({
                        "tool_name": tool_name,
                        "input": tool_input,
                        "result": error_result
                    })
                    
                    # Add error to messages (Converse API format)
                    messages.append({
                        "role": "user",
                        "content": [
                            {
                                "toolResult": {
                                    "toolUseId": tool_use_id,
                                    "content": [{"json": error_result}]
                                }
                            }
                        ]
                    })
        
        # Continue the conversation with tool results
        logger.info("=== CONTINUING CONVERSATION AFTER TOOL EXECUTION ===")
        final_response = self._call_claude_with_tools(messages)
        
        # Log thinking from the final response after tool execution
        logger.info("=== LOGGING THINKING AFTER TOOL EXECUTION ===")
        _extract_and_log_thinking(final_response, f"after_tool_execution_{_call_tracker['total_tool_calls']}")
        
        final_response["tool_results"] = tool_results
        
        return final_response
 