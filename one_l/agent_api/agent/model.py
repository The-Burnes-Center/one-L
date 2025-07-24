"""
Model integration for Claude 4 Sonnet thinking with AWS Bedrock.
Handles tool calling for comprehensive document review.
"""

import json
import boto3
import logging
from typing import Dict, Any, List
from .system_prompt import SYSTEM_PROMPT
from .tools import retrieve_from_knowledge_base, redline_document

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Initialize AWS clients
bedrock_client = boto3.client('bedrock-runtime')

# Model configuration
CLAUDE_MODEL_ARN = "anthropic.claude-sonnet-4-20250514-v1:0"
MAX_TOKENS = 8000
TEMPERATURE = 1.0  # Must be 1.0 when thinking is enabled
THINKING_BUDGET_TOKENS = 4000

class Model:
    """
    Handles document review using Claude 4 Sonnet thinking with tool calling.
    """
    
    def __init__(self, knowledge_base_id: str, region: str):
        self.knowledge_base_id = knowledge_base_id
        self.region = region
        self.tools = self._define_tools()
    
    def _define_tools(self) -> List[Dict[str, Any]]:
        """Define available tools for Claude."""
        return [
            {
                "name": "retrieve_from_knowledge_base",
                "description": "Retrieve relevant documents from the knowledge base to identify potential conflicts with vendor submission. Use multiple targeted queries for comprehensive coverage.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Search query to find relevant reference documents. Use specific terms related to vendor clauses."
                        },
                        "max_results": {
                            "type": "integer",
                            "description": "Maximum number of results to retrieve (recommended: 10-20)",
                            "default": 15
                        }
                    },
                    "required": ["query"]
                }
            }
        ]
    
    def review_document(self, document_content: str) -> Dict[str, Any]:
        """
        Review a vendor document for conflicts using Claude 4 Sonnet thinking with tools.
        
        Args:
            document_content: Text content of the vendor document
            
        Returns:
            Dictionary containing the review results
        """
        
        try:
            logger.info(f"Starting document review with Claude 4 Sonnet thinking and tools")
            
            # Prepare the conversation
            messages = [
                {
                    "role": "user",
                    "content": document_content
                }
            ]
            
            # Make the initial request to Claude with tools
            response = self._call_claude_with_tools(messages)
            
            return {
                "success": True,
                "analysis": response.get("content", ""),
                "tool_results": response.get("tool_results", []),
                "usage": response.get("usage", {}),
                "thinking": response.get("thinking", "")
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
    
    def _call_claude_with_tools(self, messages: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Call Claude 4 Sonnet thinking with tool support.
        """
        
        # Prepare the request body
        request_body = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": MAX_TOKENS,
            "temperature": TEMPERATURE,
            "system": SYSTEM_PROMPT,
            "messages": messages,
            "tools": self.tools,
            # Enable thinking for Claude 4 Sonnet
            "thinking": {
                "type": "enabled",
                "budget_tokens": THINKING_BUDGET_TOKENS
            }
        }
        
        logger.info(f"Calling Claude with {len(messages)} messages and {len(self.tools)} tools")
        
        try:
            # Call Bedrock
            response = bedrock_client.invoke_model(
                modelId=CLAUDE_MODEL_ARN,
                body=json.dumps(request_body),
                contentType="application/json",
                accept="application/json"
            )
            
            # Parse response
            response_body = json.loads(response['body'].read())
            
            # Handle tool calls if present
            if response_body.get("stop_reason") == "tool_use":
                return self._handle_tool_calls(messages, response_body)
            
            return response_body
            
        except Exception as e:
            logger.error(f"Error calling Claude: {str(e)}")
            raise
    
    def _handle_tool_calls(self, messages: List[Dict[str, Any]], claude_response: Dict[str, Any]) -> Dict[str, Any]:
        """
        Handle tool calls from Claude and continue the conversation.
        """
        
        # Add Claude's response to messages
        messages.append({
            "role": "assistant",
            "content": claude_response["content"]
        })
        
        tool_results = []
        
        # Process each tool call
        for content_block in claude_response["content"]:
            if content_block.get("type") == "tool_use":
                tool_name = content_block["name"]
                tool_input = content_block["input"]
                tool_use_id = content_block["id"]
                
                logger.info(f"Executing tool: {tool_name} with input: {tool_input}")
                
                # Execute the tool
                try:
                    if tool_name == "retrieve_from_knowledge_base":
                        result = retrieve_from_knowledge_base(
                            query=tool_input["query"],
                            max_results=tool_input.get("max_results", 15),
                            knowledge_base_id=self.knowledge_base_id,
                            region=self.region
                        )
                    else:
                        result = {"error": f"Unknown tool: {tool_name}"}
                    
                    tool_results.append({
                        "tool_name": tool_name,
                        "input": tool_input,
                        "result": result
                    })
                    
                    # Add tool result to messages
                    messages.append({
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": tool_use_id,
                                "content": json.dumps(result)
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
                    
                    # Add error to messages
                    messages.append({
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": tool_use_id,
                                "content": json.dumps(error_result)
                            }
                        ]
                    })
        
        # Continue the conversation with tool results
        final_response = self._call_claude_with_tools(messages)
        final_response["tool_results"] = tool_results
        
        return final_response 