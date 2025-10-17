"""
Model integration for Claude 4 Sonnet thinking with AWS Bedrock.
Handles tool calling for comprehensive document review.
"""

import json
import boto3
import logging
import time
from typing import Dict, Any, List
from .system_prompt import SYSTEM_PROMPT
from .tools import retrieve_from_knowledge_base, redline_document, get_tool_definitions, save_analysis_to_dynamodb, parse_conflicts_for_redlining

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Initialize AWS clients
bedrock_client = boto3.client('bedrock-runtime')

# Model configuration - Hybrid approach with Haiku 4.5 for preprocessing and Sonnet 4.5 for redlining
CLAUDE_HAIKU_MODEL_ID = "anthropic.claude-haiku-4-5-20251001-v1:0"  # Fast preprocessing
CLAUDE_SONNET_MODEL_ID = "anthropic.claude-sonnet-4-5-20250929-v1:0"    # Sophisticated redlining
# Fallback models (older, more stable versions)
CLAUDE_HAIKU_FALLBACK_ID = "anthropic.claude-3-5-haiku-20241022-v1:0"  # Claude 3.5 Haiku fallback
CLAUDE_SONNET_FALLBACK_ID = "anthropic.claude-3-5-sonnet-20241022-v2:0"  # Claude 3.5 Sonnet v2 fallback
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

class Model:
    """
    Handles document review using hybrid approach: Haiku 4.5 for preprocessing, Sonnet 4.5 for redlining.
    """
    
    def __init__(self, knowledge_base_id: str, region: str):
        self.knowledge_base_id = knowledge_base_id
        self.region = region
        self.tools = get_tool_definitions()
    

    
    def review_document(self, bucket_type: str, document_s3_key: str) -> Dict[str, Any]:
        """
        Review a vendor document using hybrid approach: Haiku 4.5 preprocessing + Sonnet 4.5 redlining.
        
        Args:
            bucket_type: Type of source bucket
            document_s3_key: S3 key of the document to review
            
        Returns:
            Dictionary containing the review results
        """
        
        try:
            logger.info(f"Starting hybrid document review: Haiku 4.5 preprocessing + Sonnet 4.5 redlining")
            
            # Step 1: Preprocessing with Haiku 4.5 (fast, less throttling)
            preprocessing_result = self._preprocess_document_with_haiku(bucket_type, document_s3_key)
            if not preprocessing_result["success"]:
                return preprocessing_result
            
            # Step 2: Redlining with Sonnet 4.5 (sophisticated analysis)
            redlining_result = self._redline_document_with_sonnet(preprocessing_result)
            if not redlining_result["success"]:
                return redlining_result
            
            # Combine results
            final_result = {
                "success": True,
                "preprocessing": preprocessing_result,
                "redlining": redlining_result,
                "analysis": redlining_result.get("analysis", ""),
                "tool_results": preprocessing_result.get("tool_results", []) + redlining_result.get("tool_results", []),
                "usage": {
                    "haiku": preprocessing_result.get("usage", {}),
                    "sonnet": redlining_result.get("usage", {})
                },
                "conflicts_count": redlining_result.get("conflicts_count", 0)
            }
            
            logger.info(f"HYBRID DOCUMENT REVIEW COMPLETE - Haiku preprocessing + Sonnet redlining successful")
            return final_result
            
        except Exception as e:
            logger.error(f"Error in hybrid document review: {str(e)}")
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
    
    def _call_claude_with_tools(self, messages: List[Dict[str, Any]], retry_count: int = 0, model_id: str = None, use_fallback: bool = False) -> Dict[str, Any]:
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
            if use_fallback:
                # Use fallback models if primary models fail
                if model_id == CLAUDE_HAIKU_MODEL_ID:
                    selected_model = CLAUDE_HAIKU_FALLBACK_ID
                    logger.info(f"Using Haiku fallback model: {selected_model}")
                elif model_id == CLAUDE_SONNET_MODEL_ID:
                    selected_model = CLAUDE_SONNET_FALLBACK_ID
                    logger.info(f"Using Sonnet fallback model: {selected_model}")
                else:
                    selected_model = CLAUDE_SONNET_FALLBACK_ID  # Default fallback
                    logger.info(f"Using default Sonnet fallback model: {selected_model}")
            else:
                # Use primary models
                selected_model = model_id if model_id else CLAUDE_SONNET_MODEL_ID
                logger.info(f"Using primary model: {selected_model}")
            
            response = bedrock_client.converse(
                modelId=selected_model,
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
                    return self._call_claude_with_tools(messages, retry_count + 1, model_id, use_fallback)
                else:
                    # Try fallback model if not already using it
                    if not use_fallback:
                        logger.warning(f"Primary model throttled, trying fallback model")
                        return self._call_claude_with_tools(messages, 0, model_id, use_fallback=True)
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
    
    def _preprocess_document_with_haiku(self, bucket_type: str, document_s3_key: str) -> Dict[str, Any]:
        """
        Preprocess document with Haiku 4.5 for fast analysis and knowledge base queries.
        
        Args:
            bucket_type: Type of source bucket
            document_s3_key: S3 key of the document to review
            
        Returns:
            Dictionary containing preprocessing results
        """
        try:
            logger.info(f"Starting Haiku 4.5 preprocessing for document: {document_s3_key}")
            
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
            
            messages = [
                {
                    "role": "user",
                    "content": [
                        {
                            "text": "Please perform initial analysis of this vendor submission document. Focus on:\n1. Document structure and key sections\n2. Identify potential legal terms and clauses\n3. Extract key information for knowledge base queries\n4. Provide a structured summary for further analysis"
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
            
            # Call Haiku 4.5 for preprocessing
            response = self._call_claude_with_tools(messages, model_id=CLAUDE_HAIKU_MODEL_ID)
            
            # Extract content from response
            content = ""
            if response.get("output", {}).get("message", {}).get("content"):
                for content_block in response["output"]["message"]["content"]:
                    if content_block.get("text"):
                        content += content_block["text"]
            
            logger.info(f"Haiku 4.5 preprocessing completed successfully")
            
            return {
                "success": True,
                "analysis": content,
                "tool_results": response.get("tool_results", []),
                "usage": response.get("usage", {}),
                "document_s3_key": document_s3_key,
                "bucket_type": bucket_type,
                "filename": filename
            }
            
        except Exception as e:
            logger.error(f"Error in Haiku preprocessing: {str(e)}")
            return {
                "success": False,
                "error": str(e),
                "analysis": "",
                "tool_results": [],
                "usage": {}
            }
    
    def _redline_document_with_sonnet(self, preprocessing_result: Dict[str, Any]) -> Dict[str, Any]:
        """
        Perform sophisticated redlining with Sonnet 4.5 using preprocessing results.
        
        Args:
            preprocessing_result: Results from Haiku preprocessing
            
        Returns:
            Dictionary containing redlining results
        """
        try:
            logger.info(f"Starting Sonnet 4.5 redlining using preprocessing results")
            
            # Get document from S3 for attachment
            bucket_name = self._get_bucket_name(preprocessing_result["bucket_type"])
            s3_client = boto3.client('s3')
            
            response = s3_client.get_object(Bucket=bucket_name, Key=preprocessing_result["document_s3_key"])
            document_data = response['Body'].read()
            
            # Prepare enhanced conversation with preprocessing context
            messages = [
                {
                    "role": "user",
                    "content": [
                        {
                            "text": f"""Based on the preprocessing analysis below, perform comprehensive legal redlining of this vendor submission document.

PREPROCESSING ANALYSIS:
{preprocessing_result.get("analysis", "")}

Please:
1. Perform detailed legal analysis against Massachusetts state requirements
2. Identify specific conflicts and compliance issues
3. Generate detailed redlining annotations
4. Provide comprehensive conflict resolution recommendations
5. Create structured output for document markup"""
                        },
                        {
                            "document": {
                                "format": self._get_document_format(preprocessing_result["document_s3_key"]),
                                "name": preprocessing_result["filename"],
                                "source": {
                                    "bytes": document_data
                                }
                            }
                        }
                    ]
                }
            ]
            
            # Call Sonnet 4.5 for sophisticated redlining
            response = self._call_claude_with_tools(messages, model_id=CLAUDE_SONNET_MODEL_ID)
            
            # Extract content from response
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
            
            logger.info(f"Sonnet 4.5 redlining completed successfully with {conflicts_count} conflicts detected")
            
            return {
                "success": True,
                "analysis": content,
                "tool_results": response.get("tool_results", []),
                "usage": response.get("usage", {}),
                "thinking": response.get("thinking", ""),
                "conflicts_count": conflicts_count
            }
            
        except Exception as e:
            logger.error(f"Error in Sonnet redlining: {str(e)}")
            return {
                "success": False,
                "error": str(e),
                "analysis": "",
                "tool_results": [],
                "usage": {},
                "thinking": ""
            }
 