"""
Model integration for Claude 4 Sonnet thinking with AWS Bedrock.
Handles tool calling for comprehensive document review.
"""

import json
import boto3
from botocore.config import Config
import logging
import time
from typing import Dict, Any, List
from .system_prompt import SYSTEM_PROMPT
from .tools import retrieve_from_knowledge_base, redline_document, get_tool_definitions, save_analysis_to_dynamodb, parse_conflicts_for_redlining

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
        
        # Create a new minimal document structure
        chunk_doc = Document()
        
        # Copy the essential document structure
        chunk_doc.settings = doc.settings
        if hasattr(doc, 'styles') and hasattr(doc.styles, '_document'):
            chunk_doc.styles._document = doc.styles._document
        
        # Copy paragraphs
        for i in range(start_idx, end_idx):
            src_para = doc.paragraphs[i]
            new_para = chunk_doc.add_paragraph()
            # Copy the paragraph content
            for run in src_para.runs:
                new_run = new_para.add_run(run.text)
                new_run.bold = run.bold
                new_run.italic = run.italic
                new_run.underline = run.underline
                # Copy font properties if they exist
                if run.font and run.font.color:
                    from docx.shared import RGBColor
                    if isinstance(run.font.color.rgb, tuple):
                        new_run.font.color.rgb = RGBColor(*run.font.color.rgb)
        
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
            try:
                from docx import Document
                import io
                doc = Document(io.BytesIO(document_data))
                total_paragraphs = len(doc.paragraphs)
                logger.info(f"Document has {total_paragraphs} paragraphs (approximately {total_paragraphs//20} pages)")
                
                # If document is very large (>100 paragraphs ≈ 5+ pages), split into chunks
                if total_paragraphs > 100:
                    logger.warning(f"Large document detected ({total_paragraphs} paragraphs). Splitting into chunks for comprehensive analysis.")
                    return self._review_document_chunked(doc, document_data, document_s3_key, bucket_type, filename)
                else:
                    instruction_text = "Please analyze this vendor submission document completely, including all pages and sections."
            except Exception as e:
                logger.warning(f"Could not pre-analyze document structure: {e}")
                instruction_text = "Please analyze this vendor submission document completely."
            
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
            response = self._call_claude_with_tools(messages)
            
            # Extract content from Converse API response
            content = ""
            if response.get("output", {}).get("message", {}).get("content"):
                for content_block in response["output"]["message"]["content"]:
                    if content_block.get("text"):
                        content += content_block["text"]
            
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
            
            return {
                "success": True,
                "analysis": content,
                "tool_results": response.get("tool_results", []),
                "usage": response.get("usage", {}),
                "thinking": response.get("thinking", ""),
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
            
            # Process each chunk
            for chunk_info in chunks:
                chunk_num = chunk_info['chunk_num']
                start_para = chunk_info['start_para']
                end_para = chunk_info['end_para']
                chunk_bytes = chunk_info['bytes']
                
                logger.info(f"Analyzing chunk {chunk_num + 1}/{len(chunks)} (paragraphs {start_para}-{end_para})")
                
                # Create instruction for this specific chunk
                approx_pages = f"(approximately pages {start_para//20}-{end_para//20})"
                instruction_text = f"Analyze this vendor submission section {approx_pages}. Focus on this specific portion of the document. Find ALL conflicts in this section."
                
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
                response = self._call_claude_with_tools(messages)
                
                # Extract content
                content = ""
                if response.get("output", {}).get("message", {}).get("content"):
                    for content_block in response["output"]["message"]["content"]:
                        if content_block.get("text"):
                            content += content_block["text"]
                
                all_content.append(content)
                all_tool_results.append(response.get("tool_results", []))
                
                # Track usage
                if response.get("usage"):
                    total_tokens_used += response.get("usage", {}).get("totalTokens", 0)
                
                # Count conflicts in this chunk
                try:
                    conflicts = parse_conflicts_for_redlining(content)
                    all_conflicts.extend(conflicts)
                    logger.info(f"Found {len(conflicts)} conflicts in chunk {chunk_num + 1}")
                except Exception as e:
                    logger.warning(f"Error parsing conflicts from chunk {chunk_num + 1}: {str(e)}")
            
            # Merge all content
            merged_content = "\n\n--- ANALYSIS CONTINUED FROM NEXT SECTION ---\n\n".join(all_content)
            
            # Log final summary
            total_conflicts = len(all_conflicts)
            _call_tracker['total_conflicts_detected'] += total_conflicts
            
            logger.info(f"CHUNKED DOCUMENT REVIEW COMPLETE - Total Chunks: {len(chunks)}, Total Conflicts: {total_conflicts}, Total Tokens Used: {total_tokens_used}")
            
            return {
                "success": True,
                "analysis": merged_content,
                "tool_results": all_tool_results,
                "usage": {"totalTokens": total_tokens_used},
                "thinking": "",
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
        final_response = self._call_claude_with_tools(messages)
        final_response["tool_results"] = tool_results
        
        return final_response
 