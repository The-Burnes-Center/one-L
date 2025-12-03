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
import sys
import os
from typing import Dict, Any, List
from .system_prompt import SYSTEM_PROMPT
from .tools import retrieve_from_knowledge_base, redline_document, get_tool_definitions, save_analysis_to_dynamodb, parse_conflicts_for_redlining

# Import constants - add parent directories to path
_parent_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if _parent_dir not in sys.path:
    sys.path.insert(0, _parent_dir)
try:
    import constants
except ImportError:
    # Fallback if constants not available
    class Constants:
        CHUNK_SIZE_CHARACTERS = 20000
        CHUNK_OVERLAP_CHARACTERS = 2000
    constants = Constants()

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
# Fallback: Claude Sonnet 4 with 1M context (same model ID, requires anthropic_beta parameter)
# After 1M fails, we retry the original Sonnet 4
ANTHROPIC_BETA_1M = "context-1m-2025-08-07"  # Beta parameter to enable 1M context window
TEMPERATURE = 1.0  # Must be 1.0 when thinking is enabled
THINKING_BUDGET_TOKENS = 32000  # Increased to 32k for more complex reasoning in document review
MAX_TOKENS = 64000  # Maximum output tokens - must be greater than THINKING_BUDGET_TOKENS per AWS Bedrock requirements

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
    # Based on actual API responses, thinking is in content blocks as "reasoningContent"
    
    # Method 1: Check for reasoningContent in content blocks - AWS Bedrock Converse API actual location
    # This is where AWS Bedrock returns thinking when enabled via additionalModelRequestFields
    # Check this FIRST since it's the actual location based on API responses
    if response.get("output", {}).get("message", {}).get("content"):
        for idx, content_block in enumerate(response.get("output", {}).get("message", {}).get("content", [])):
            # Check for reasoningContent (the actual field name AWS Bedrock uses)
            if "reasoningContent" in content_block:
                reasoning_val = content_block.get("reasoningContent")
                if isinstance(reasoning_val, str) and reasoning_val:
                    thinking_content = reasoning_val
                    logger.info(f"Found thinking via Method 1: content_block[{idx}].reasoningContent (string, length: {len(thinking_content)})")
                    break
                elif isinstance(reasoning_val, dict):
                    # Handle structured reasoningContent - AWS Bedrock structure: reasoningContent.reasoningText.text
                    reasoning_text_obj = reasoning_val.get("reasoningText")
                    if isinstance(reasoning_text_obj, dict):
                        # reasoningText is a dict with 'text' and 'signature' fields
                        if isinstance(reasoning_text_obj.get("text"), str):
                            thinking_content = reasoning_text_obj.get("text", "")
                            logger.info(f"Found thinking via Method 1: content_block[{idx}].reasoningContent.reasoningText.text (length: {len(thinking_content)})")
                            break
                    elif isinstance(reasoning_text_obj, str):
                        # Fallback: reasoningText might be a string directly
                        thinking_content = reasoning_text_obj
                        logger.info(f"Found thinking via Method 1: content_block[{idx}].reasoningContent.reasoningText (string, length: {len(thinking_content)})")
                        break
                    # Additional fallbacks
                    elif isinstance(reasoning_val.get("text"), str):
                        thinking_content = reasoning_val.get("text", "")
                        logger.info(f"Found thinking via Method 1: content_block[{idx}].reasoningContent.text")
                        break
                    elif isinstance(reasoning_val.get("content"), str):
                        thinking_content = reasoning_val.get("content", "")
                        logger.info(f"Found thinking via Method 1: content_block[{idx}].reasoningContent.content")
                        break
    
    # Method 2: Direct thinking field (string) - fallback location
    if not thinking_content and isinstance(response.get("thinking"), str) and response.get("thinking"):
        thinking_content = response.get("thinking", "")
        logger.info(f"Found thinking via Method 2: direct thinking field")
    
    # Method 3: Thinking in output.thinking (string) - alternative location
    # Check if thinking key exists first (even if None or empty)
    elif not thinking_content and response.get("output"):
        output = response.get("output", {})
        if "thinking" in output:
            thinking_val = output.get("thinking")
            if isinstance(thinking_val, str):
                if thinking_val and thinking_val.strip():  # Only use if not empty or whitespace
                    thinking_content = thinking_val
                    logger.info(f"Found thinking via Method 3: output.thinking (length: {len(thinking_content)})")
                else:  # Empty string means thinking was enabled but not used
                    logger.info(f"DEBUG: output.thinking exists but is empty string (thinking enabled but not used)")
            elif thinking_val is None:
                logger.info(f"DEBUG: output.thinking exists but is None")
            else:
                logger.info(f"DEBUG: output.thinking exists but is unexpected type: {type(thinking_val)}")
    
    # Method 4: Structured thinking object with content/text fields
    elif not thinking_content and isinstance(response.get("thinking"), dict):
        thinking_obj = response.get("thinking", {})
        if isinstance(thinking_obj.get("content"), str):
            thinking_content = thinking_obj.get("content", "")
            logger.info(f"Found thinking via Method 4: thinking.content")
        elif isinstance(thinking_obj.get("text"), str):
            thinking_content = thinking_obj.get("text", "")
            logger.info(f"Found thinking via Method 4: thinking.text")
        elif isinstance(thinking_obj.get("thinking"), str):
            thinking_content = thinking_obj.get("thinking", "")
            logger.info(f"Found thinking via Method 4: thinking.thinking")
    
    # Method 5: Thinking in output.thinking as object
    elif not thinking_content and isinstance(response.get("output", {}).get("thinking"), dict):
        thinking_obj = response.get("output", {}).get("thinking", {})
        if isinstance(thinking_obj.get("content"), str):
            thinking_content = thinking_obj.get("content", "")
            logger.info(f"Found thinking via Method 5: output.thinking.content")
        elif isinstance(thinking_obj.get("text"), str):
            thinking_content = thinking_obj.get("text", "")
            logger.info(f"Found thinking via Method 5: output.thinking.text")
        elif isinstance(thinking_obj.get("thinking"), str):
            thinking_content = thinking_obj.get("thinking", "")
            logger.info(f"Found thinking via Method 5: output.thinking.thinking")
    
    # Method 6: Fallback - Check for thinking field in content blocks (older API versions)
    if not thinking_content and response.get("output", {}).get("message", {}).get("content"):
        for idx, content_block in enumerate(response.get("output", {}).get("message", {}).get("content", [])):
            if content_block.get("thinking"):
                if isinstance(content_block.get("thinking"), str):
                    thinking_content = content_block.get("thinking", "")
                    logger.info(f"Found thinking via Method 6 (fallback): content_block[{idx}].thinking (string)")
                    break
                elif isinstance(content_block.get("thinking"), dict):
                    thinking_obj = content_block.get("thinking", {})
                    if isinstance(thinking_obj.get("content"), str):
                        thinking_content = thinking_obj.get("content", "")
                        logger.info(f"Found thinking via Method 6 (fallback): content_block[{idx}].thinking.content")
                        break
                    elif isinstance(thinking_obj.get("text"), str):
                        thinking_content = thinking_obj.get("text", "")
                        logger.info(f"Found thinking via Method 6 (fallback): content_block[{idx}].thinking.text")
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
        # Thinking not found - this is normal for tool call responses and continuation responses
        # AWS Bedrock typically only returns thinking in the initial response
        # Only log warning for initial calls, use debug for tool calls/continuations
        if "tool_call" in context or "after_tool" in context:
            logger.debug(f"No thinking content found for context: {context} (normal - thinking typically only in initial response)")
        else:
            logger.debug(f"No thinking content found for context: {context}")
    
    return thinking_content if thinking_content else ""

def _extract_json_only(content: str) -> str:
    """
    Extract only JSON object or array from response content, stripping any explanatory text.
    This ensures we only return valid JSON even if the agent adds explanatory text.
    Prioritizes new format (object with explanation and conflicts) over old format (array).
    
    Args:
        content: Raw response content that may contain JSON plus explanatory text
        
    Returns:
        Clean JSON string (object or array) or empty object/array if no valid JSON found
    """
    if not content:
        return '{"explanation": "", "conflicts": []}'
    
    content_trimmed = content.strip()
    
    # First, try to find JSON object pattern (new format with explanation and conflicts)
    json_object_match = re.search(r'\{[\s\S]*?"conflicts"[\s\S]*?\}', content)
    if json_object_match:
        json_str = json_object_match.group(0)
        # Validate it's actually valid JSON
        try:
            parsed = json.loads(json_str)
            if isinstance(parsed, dict) and "conflicts" in parsed:
                logger.info(f"Extracted valid JSON object from response (length: {len(json_str)} chars)")
                return json_str
        except json.JSONDecodeError:
            logger.warning("Found object-like pattern but not valid JSON, trying bracket matching...")
    
    # Try to find JSON object by bracket matching (for new format)
    start_idx = content_trimmed.find('{')
    if start_idx != -1:
        # Try to parse from { to end, or find matching }
        brace_count = 0
        end_idx = start_idx
        for i in range(start_idx, len(content_trimmed)):
            if content_trimmed[i] == '{':
                brace_count += 1
            elif content_trimmed[i] == '}':
                brace_count -= 1
                if brace_count == 0:
                    end_idx = i + 1
                    break
        
        if brace_count == 0 and end_idx > start_idx:
            json_str = content_trimmed[start_idx:end_idx]
            try:
                parsed = json.loads(json_str)
                if isinstance(parsed, dict):
                    logger.info(f"Extracted JSON object by bracket matching (length: {len(json_str)} chars)")
                    return json_str
            except json.JSONDecodeError:
                pass
    
    # Fallback: try to find JSON array pattern (backwards compatibility)
    json_array_match = re.search(r'\[[\s\S]*?\]', content)
    if json_array_match:
        json_str = json_array_match.group(0)
        # Validate it's actually valid JSON
        try:
            parsed = json.loads(json_str)
            if isinstance(parsed, list):
                logger.info(f"Extracted valid JSON array from response (backwards compatibility, length: {len(json_str)} chars)")
                # Convert array to new format for consistency
                return json.dumps({"explanation": "", "conflicts": parsed})
        except json.JSONDecodeError:
            logger.warning("Found array-like pattern but not valid JSON, trying bracket matching...")
    
    # Try to find JSON array by bracket matching (backwards compatibility)
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
                parsed = json.loads(json_str)
                if isinstance(parsed, list):
                    logger.info(f"Extracted JSON array by bracket matching (backwards compatibility, length: {len(json_str)} chars)")
                    # Convert array to new format for consistency
                    return json.dumps({"explanation": "", "conflicts": parsed})
            except json.JSONDecodeError:
                pass
    
    # If all else fails, log warning and return empty object
    logger.warning(f"Could not extract valid JSON from response. Response preview: {content[:200]}...")
    return '{"explanation": "", "conflicts": []}'

def _split_document_into_chunks(doc, chunk_size_characters=20000, chunk_overlap_characters=2000, is_pdf=False, pdf_bytes=None):
    """
    Split a document into chunks using character-based chunking.
    
    Args:
        doc: python-docx Document object (for DOCX) or None (for PDF)
        chunk_size_characters: Number of characters per chunk
        chunk_overlap_characters: Number of characters to overlap between chunks
        is_pdf: Whether this is a PDF document
        pdf_bytes: PDF file content as bytes (required if is_pdf=True)
        
    Returns:
        List of chunk dictionaries with bytes, chunk_num, start_char, end_char, is_pdf
    """
    from docx import Document
    import io
    
    # Import constants
    try:
        import constants
        chunk_size = getattr(constants, 'CHUNK_SIZE_CHARACTERS', chunk_size_characters)
        overlap = getattr(constants, 'CHUNK_OVERLAP_CHARACTERS', chunk_overlap_characters)
    except (ImportError, AttributeError):
        # Fallback to function parameters
        chunk_size = chunk_size_characters
        overlap = chunk_overlap_characters
    
    chunks = []
    
    if is_pdf and pdf_bytes:
        # For PDFs: Extract text and chunk by characters
        try:
            from .pdf_processor import PDFProcessor
            pdf_processor = PDFProcessor()
            full_text = pdf_processor.extract_text(pdf_bytes)
        except Exception as e:
            logger.error(f"Error extracting text from PDF: {e}")
            # Fallback: return entire PDF as single chunk
            chunks.append({
                'bytes': pdf_bytes,
                'chunk_num': 0,
                'start_char': 0,
                'end_char': len(pdf_bytes),  # Approximate for PDF
                'is_pdf': True
            })
            return chunks
        
        total_chars = len(full_text)
        start_char = 0
        chunk_num = 0
        
        while start_char < total_chars:
            end_char = min(start_char + chunk_size, total_chars)
            
            # Extract chunk text
            chunk_text = full_text[start_char:end_char]
            
            # For PDFs, we'll create a DOCX chunk from the text
            chunk_doc = Document()
            # Split text into paragraphs (by newlines)
            for para_text in chunk_text.split('\n'):
                if para_text.strip():
                    chunk_doc.add_paragraph(para_text.strip())
            
            # Save to bytes
            buffer = io.BytesIO()
            chunk_doc.save(buffer)
            chunk_bytes = buffer.getvalue()
            
            chunks.append({
                'bytes': chunk_bytes,
                'chunk_num': chunk_num,
                'start_char': start_char,
                'end_char': end_char,
                'is_pdf': False  # Chunk is DOCX even if source was PDF
            })
            
            chunk_num += 1
            # Move start position with overlap
            start_char = end_char - overlap
            if start_char >= total_chars - overlap:
                break
    else:
        # For DOCX: Extract full text and chunk by characters
        # Extract all text from document
        full_text_parts = []
        for para in doc.paragraphs:
            if para.text.strip():
                full_text_parts.append(para.text)
        
        full_text = '\n\n'.join(full_text_parts)
        total_chars = len(full_text)
        
        if total_chars == 0:
            # Empty document - return single empty chunk
            buffer = io.BytesIO()
            doc.save(buffer)
            chunks.append({
                'bytes': buffer.getvalue(),
                'chunk_num': 0,
                'start_char': 0,
                'end_char': 0,
                'is_pdf': False
            })
            return chunks
        
        start_char = 0
        chunk_num = 0
        
        while start_char < total_chars:
            end_char = min(start_char + chunk_size, total_chars)
            
            # Extract chunk text
            chunk_text = full_text[start_char:end_char]
            
            # Create a new document for this chunk
            chunk_doc = Document()
            
            # Split chunk text into paragraphs and add to document
            for para_text in chunk_text.split('\n\n'):
                if para_text.strip():
                    chunk_doc.add_paragraph(para_text.strip())
            
            # Save to bytes
            buffer = io.BytesIO()
            chunk_doc.save(buffer)
            chunk_bytes = buffer.getvalue()
            
            chunks.append({
                'bytes': chunk_bytes,
                'chunk_num': chunk_num,
                'start_char': start_char,
                'end_char': end_char,
                'is_pdf': False
            })
            
            chunk_num += 1
            # Move start position with overlap
            start_char = end_char - overlap
            if start_char >= total_chars - overlap:
                break
    
    return chunks



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

class Model:
    """
    Handles document review using Claude 4 Sonnet thinking with tool calling.
    """
    
    def __init__(self, knowledge_base_id: str, region: str):
        self.knowledge_base_id = knowledge_base_id
        self.region = region
        
        # Resolve Knowledge Base ID if it is missing or a placeholder
        if (not self.knowledge_base_id or self.knowledge_base_id == "placeholder") and os.environ.get('KNOWLEDGE_BASE_NAME'):
            resolved_id = get_kb_id_by_name(os.environ.get('KNOWLEDGE_BASE_NAME'))
            if resolved_id:
                self.knowledge_base_id = resolved_id
                logger.info(f"Model initialized with resolved KB ID: {self.knowledge_base_id}")
            else:
                logger.warning(f"Failed to resolve KB ID from name: {os.environ.get('KNOWLEDGE_BASE_NAME')}")
        
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
            instruction_text = "Please analyze this vendor submission document completely, including all pages and sections. After identifying all conflicts, return the output in the expected JSON object format with 'explanation' and 'conflicts' fields."
            
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
                        instruction_text = "Please analyze this vendor submission document completely, including all pages and sections. After identifying all conflicts, return the output in the expected JSON object format with 'explanation' and 'conflicts' fields."
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
            
            # Extract and log explanation if present
            try:
                parsed_content = json.loads(content)
                if isinstance(parsed_content, dict) and "explanation" in parsed_content:
                    explanation = parsed_content.get("explanation", "")
                    if explanation:
                        logger.info(f"=== MODEL EXPLANATION ===")
                        logger.info(f"{explanation}")
                        logger.info(f"=== END MODEL EXPLANATION ===")
            except json.JSONDecodeError:
                pass  # Content might not be valid JSON yet, will be handled by parse_conflicts_for_redlining
            
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
            
            # Split document into chunks using character-based chunking
            chunks = _split_document_into_chunks(doc, is_pdf=False)
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
                start_char = chunk_info['start_char']
                end_char = chunk_info['end_char']
                chunk_bytes = chunk_info['bytes']
                
                logger.info(f"Analyzing chunk {chunk_num + 1}/{len(chunks)} (characters {start_char}-{end_char})")
                
                # Create instruction for this specific chunk with Additional counter context
                additional_context = ""
                if chunk_num > 0:  # Not the first chunk
                    additional_context = f" IMPORTANT: For conflicts that don't have a vendor-provided ID, use 'Additional-{additional_counter + 1}', 'Additional-{additional_counter + 2}', etc. (continuing from previous sections)."
                
                instruction_text = f"You are analyzing chunk {chunk_num + 1} of {len(chunks)} (characters {start_char}-{end_char}). Analyze this vendor submission section for MATERIAL conflicts with Massachusetts Commonwealth requirements. Focus on issues that have real business or legal impact - changes to obligations, risk allocation, financial terms, service delivery, or compliance requirements. Look for substantive differences that create actual risk or modify important rights. Do NOT flag minor language differences that don't change meaning. For each conflict you find, explain the practical business impact in the rationale field - what risk it creates and why it matters.{additional_context} Output ONLY a JSON object with 'explanation' and 'conflicts' fields (empty conflicts array [] if no conflicts found). Do not include any explanatory text or markdown formatting."
                
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
                
                # Extract and log chunk content before sending for analysis
                try:
                    from docx import Document
                    import io
                    chunk_doc = Document(io.BytesIO(chunk_bytes))
                    chunk_text_content = []
                    for para in chunk_doc.paragraphs:
                        if para.text.strip():
                            chunk_text_content.append(para.text.strip())
                    chunk_full_text = '\n\n'.join(chunk_text_content)
                    
                    logger.info(f"=== CHUNK {chunk_num + 1}/{len(chunks)} CONTENT (characters {start_char}-{end_char}) ===")
                    logger.info(f"Chunk size: {len(chunk_bytes)} bytes, {len(chunk_text_content)} paragraphs")
                    logger.info(f"Chunk text length: {len(chunk_full_text)} characters")
                    logger.info(f"--- CHUNK {chunk_num + 1} FULL TEXT ---")
                    if len(chunk_full_text) > 10000:
                        # If chunk is very large, log in sections
                        logger.info(f"Chunk is large ({len(chunk_full_text)} chars), logging in sections:")
                        section_size = 5000
                        for section_idx in range(0, len(chunk_full_text), section_size):
                            section_num = (section_idx // section_size) + 1
                            section_end = min(section_idx + section_size, len(chunk_full_text))
                            logger.info(f"--- Chunk {chunk_num + 1} Section {section_num} (chars {section_idx}-{section_end}) ---\n{chunk_full_text[section_idx:section_end]}")
                    else:
                        logger.info(f"{chunk_full_text}")
                    logger.info(f"=== END CHUNK {chunk_num + 1} CONTENT ===")
                except Exception as e:
                    logger.warning(f"Could not extract text content from chunk {chunk_num + 1} for logging: {e}")
                
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
                
                # Extract and log explanation if present
                try:
                    parsed_content = json.loads(content)
                    if isinstance(parsed_content, dict) and "explanation" in parsed_content:
                        explanation = parsed_content.get("explanation", "")
                        if explanation:
                            logger.info(f"=== CHUNK {chunk_num + 1} MODEL EXPLANATION ===")
                            logger.info(f"{explanation}")
                            logger.info(f"=== END CHUNK {chunk_num + 1} MODEL EXPLANATION ===")
                except json.JSONDecodeError:
                    pass  # Content might not be valid JSON yet, will be handled by parse_conflicts_for_redlining
                
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
            
            # Merge all content - combine JSON objects from all chunks into a single valid JSON object
            import re
            
            merged_json_conflicts = []
            chunk_explanations = []
            
            # Try to extract and combine JSON objects/arrays from each chunk
            # Also renumber Additional-[#] conflicts to ensure sequential numbering across chunks
            chunks_with_json = 0
            global_additional_counter = 0  # Track Additional counter across all chunks for renumbering
            
            for chunk_idx, chunk_content in enumerate(all_content):
                try:
                    # Try to parse as JSON object first (new format)
                    parsed_chunk = json.loads(chunk_content)
                    
                    if isinstance(parsed_chunk, dict) and "conflicts" in parsed_chunk:
                        # New format with explanation and conflicts
                        chunk_explanation = parsed_chunk.get("explanation", "")
                        if chunk_explanation:
                            chunk_explanations.append(f"Chunk {chunk_idx + 1}: {chunk_explanation}")
                        
                        chunk_conflicts = parsed_chunk.get("conflicts", [])
                        if isinstance(chunk_conflicts, list):
                            # Handle empty arrays (valid JSON response when no conflicts)
                            if len(chunk_conflicts) == 0:
                                logger.info(f"Chunk {chunk_idx + 1} returned empty conflicts array (no conflicts found)")
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
                    elif isinstance(parsed_chunk, list):
                        # Backwards compatibility: old array format
                        chunk_conflicts = parsed_chunk
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
                            logger.info(f"Merged {len(chunk_conflicts)} conflicts from chunk {chunk_idx + 1} JSON array (backwards compatibility, Additional counter: {global_additional_counter})")
                        chunks_with_json += 1
                except json.JSONDecodeError as e:
                    logger.warning(f"Failed to parse JSON from chunk {chunk_idx + 1}, trying regex extraction: {e}")
                    # Fallback: try regex extraction (backwards compatibility)
                    json_match = re.search(r'\[[\s\S]*\]', chunk_content)
                    if json_match:
                        try:
                            json_str = json_match.group(0)
                            chunk_conflicts = json.loads(json_str)
                            if isinstance(chunk_conflicts, list):
                                merged_json_conflicts.extend(chunk_conflicts)
                                chunks_with_json += 1
                        except json.JSONDecodeError:
                            pass
            
            # If we successfully found and merged JSON, create a single valid JSON object
            if chunks_with_json > 0:
                # Combine explanations from all chunks
                combined_explanation = " ".join(chunk_explanations) if chunk_explanations else "Analysis completed across multiple document sections."
                
                # Create final JSON object with explanation and conflicts
                merged_content = json.dumps({
                    "explanation": combined_explanation,
                    "conflicts": merged_json_conflicts
                }, indent=2, ensure_ascii=False)
                
                logger.info(f"Successfully merged {len(merged_json_conflicts)} total conflicts from {chunks_with_json} chunks into single JSON object")
                if chunk_explanations:
                    logger.info(f"=== COMBINED EXPLANATION FROM ALL CHUNKS ===")
                    logger.info(f"{combined_explanation}")
                    logger.info(f"=== END COMBINED EXPLANATION ===")
            else:
                # Fallback: if no JSON found, merge as text (backwards compatibility)
                merged_content = "\n\n--- ANALYSIS CONTINUED FROM NEXT SECTION ---\n\n".join(all_content)
                logger.warning("No JSON found in chunks, using text merge fallback")
            
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
    
    def _call_claude_with_tools(self, messages: List[Dict[str, Any]], retry_count: int = 0, use_1m_context: bool = False, tried_1m: bool = False) -> Dict[str, Any]:
        """
        Call Claude with tool support using Converse API.
        Implements graceful queuing with call spacing to prevent token rate limiting.
        Supports fallback: Primary Sonnet 4 (once) -> Sonnet 4 1M -> Retry Sonnet 4 with backoff
        
        Args:
            messages: List of message dictionaries
            retry_count: Current retry attempt number
            use_1m_context: Whether to use 1M context version (fallback)
            tried_1m: Whether we've already attempted 1M context (prevents loops)
        """
        
        # Implement graceful call spacing to prevent token rate limiting
        current_time = time.time()
        time_since_last_call = current_time - _call_tracker['last_call_time']
        
        if time_since_last_call < CALL_SPACING_DELAY:
            wait_time = CALL_SPACING_DELAY - time_since_last_call
            logger.info(f"Graceful queuing: waiting {wait_time:.2f}s to prevent token rate limiting")
            time.sleep(wait_time)
        
        # Always use Claude Sonnet 4 - just toggle 1M context via beta parameter
        current_model_id = CLAUDE_MODEL_ID
        
        # Log attempt (but don't increment counter until successful)
        model_name = "Sonnet 4 1M" if use_1m_context else "Sonnet 4"
        logger.info(f"Calling Claude ({model_name}: {current_model_id}) with {len(messages)} messages and {len(self.tools)} tools (attempt {retry_count + 1}, use_1m_context={use_1m_context}) - Total successful calls so far: {_call_tracker['total_model_calls']}")
        
        try:
            # Prepare inference config
            inference_config = {
                "temperature": TEMPERATURE,
                "maxTokens": MAX_TOKENS
            }
            
            # Prepare API call parameters
            api_params = {
                "modelId": current_model_id,
                "messages": messages,
                "system": [{"text": SYSTEM_PROMPT}],
                "inferenceConfig": inference_config,
                "toolConfig": {"tools": self.tools},
                "additionalModelRequestFields": {
                    "thinking": {
                        "type": "enabled",
                        "budget_tokens": THINKING_BUDGET_TOKENS
                    }
                }
            }
            
            # Add 1M context beta parameter if using 1M fallback
            # AWS Bedrock expects anthropic_beta to be a list of strings
            if use_1m_context:
                api_params["additionalModelRequestFields"]["anthropic_beta"] = [ANTHROPIC_BETA_1M]
            
            # Call Bedrock using Converse API (supports document attachments)
            response = bedrock_client.converse(**api_params)
            
            # SUCCESS: Only now increment the counter for successful calls
            _call_tracker['total_model_calls'] += 1
            _call_tracker['last_call_time'] = time.time()
            
            logger.info(f"Claude API call successful! Total successful model calls: {_call_tracker['total_model_calls']}")
            
            # Extract and log thinking content (only the reasoning text will be logged)
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
            error_type = type(e).__name__
            
            # Check for throttling errors and implement exponential backoff
            is_throttling = ("ThrottlingException" in error_msg or "Too many tokens" in error_msg or 
                            "rate" in error_msg.lower() or "throttl" in error_msg.lower() or
                            "limit" in error_msg.lower())
            
            # Check for transient errors that should be retried
            is_transient = (
                "ServiceUnavailableException" in error_type or
                "InternalServerError" in error_type or
                "InternalFailure" in error_msg or
                "ServiceUnavailable" in error_msg or
                "timeout" in error_msg.lower() or
                "Timeout" in error_type or
                "Connection" in error_type or
                "ReadTimeout" in error_type or
                "502" in error_msg or  # Bad Gateway
                "503" in error_msg or  # Service Unavailable
                "504" in error_msg     # Gateway Timeout
            )
            
            # Check for validation errors that should NOT be retried (configuration issues)
            is_validation_error = (
                "ValidationException" in error_type or
                "ValidationError" in error_type or
                "InvalidParameter" in error_type or
                "InvalidRequest" in error_msg
            )
            
            # Retry logic for throttling and transient errors
            if (is_throttling or is_transient) and not is_validation_error:
                # On first failure (retry_count=0), try Sonnet 4 1M if not already tried
                if retry_count == 0 and not use_1m_context and not tried_1m:
                    logger.warning(f"Sonnet 4 failed on first attempt. Attempting fallback to Sonnet 4 1M")
                    try:
                        return self._call_claude_with_tools(messages, retry_count=0, use_1m_context=True, tried_1m=True)
                    except Exception as fallback_1m_error:
                        # If 1M fails, go back to Sonnet 4 and continue retrying with backoff
                        logger.warning(f"Sonnet 4 1M also failed. Retrying Sonnet 4 with exponential backoff")
                        delay = min(BASE_DELAY * (BACKOFF_MULTIPLIER ** retry_count), MAX_DELAY)
                        error_category = "throttling" if is_throttling else "transient"
                        logger.warning(f"Retrying Sonnet 4 in {delay} seconds (attempt {retry_count + 2})")
                        time.sleep(delay)
                        return self._call_claude_with_tools(messages, retry_count + 1, use_1m_context=False, tried_1m=True)
                
                # Continue retrying Sonnet 4 with exponential backoff
                if retry_count < MAX_RETRIES:
                    delay = min(BASE_DELAY * (BACKOFF_MULTIPLIER ** retry_count), MAX_DELAY)
                    error_category = "throttling" if is_throttling else "transient"
                    logger.warning(f"Claude API {error_category} error detected ({error_type}), retrying in {delay} seconds (attempt {retry_count + 1}/{MAX_RETRIES + 1})")
                    time.sleep(delay)
                    return self._call_claude_with_tools(messages, retry_count + 1, use_1m_context=False, tried_1m=tried_1m)
                else:
                    # Max retries exceeded
                    error_category = "throttling" if is_throttling else "transient"
                    logger.error(f"Max retries exceeded for Claude API {error_category} error after {MAX_RETRIES + 1} attempts")
                    raise Exception(f"Claude API {error_category} error limit exceeded after {MAX_RETRIES + 1} retries: {str(e)}")
            else:
                # For validation errors, try Sonnet 4 1M if not already tried, then retry Sonnet 4
                if not use_1m_context and is_validation_error and not tried_1m:
                    logger.warning(f"Validation error on Sonnet 4. Attempting fallback to Sonnet 4 1M")
                    try:
                        return self._call_claude_with_tools(messages, retry_count=0, use_1m_context=True, tried_1m=True)
                    except Exception as fallback_1m_error:
                        # If 1M also fails, retry original Sonnet 4
                        logger.warning(f"Sonnet 4 1M also failed with validation error. Retrying original Sonnet 4")
                        try:
                            return self._call_claude_with_tools(messages, retry_count=0, use_1m_context=False, tried_1m=True)
                        except Exception as final_error:
                            logger.error(f"All attempts failed with validation errors. Sonnet 4: {str(e)}, Sonnet 4 1M: {str(fallback_1m_error)}, Sonnet 4 retry: {str(final_error)}")
                            raise Exception(f"Claude API validation error on all attempts. Sonnet 4: {str(e)}, Sonnet 4 1M: {str(fallback_1m_error)}, Sonnet 4 retry: {str(final_error)}")
                
                # Non-retryable error - don't retry
                logger.error(f"Error calling Claude (non-retryable {error_type}): {str(e)}")
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
 
