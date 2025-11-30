"""
Pydantic models for validating all Claude model outputs in Step Functions workflow.
All models use strict validation with extra='forbid' to reject extra fields.
"""

from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field, field_validator, ConfigDict


class QueryModel(BaseModel):
    """Model for individual KB query."""
    query: str = Field(..., min_length=10, description="Query string for knowledge base search")
    section: Optional[str] = Field(None, description="Document section this query targets")
    max_results: int = Field(default=50, ge=1, le=100, description="Maximum results to return")
    query_id: Optional[int] = Field(None, description="Optional query identifier")
    
    model_config = ConfigDict(
        extra='forbid',
        str_strip_whitespace=True
    )


class ChunkStructureModel(BaseModel):
    """Model for chunk structure metadata."""
    sections: List[str] = Field(default_factory=list, description="List of document sections in this chunk")
    vendor_exceptions: List[Dict[str, Any]] = Field(default_factory=list, description="Vendor exceptions found in chunk")
    document_references: List[str] = Field(default_factory=list, description="Massachusetts documents referenced")
    character_range: str = Field(..., description="Character range for this chunk, e.g., 'characters 0-100000'")
    
    model_config = ConfigDict(
        extra='forbid',
        str_strip_whitespace=True
    )


class StructureAnalysisOutput(BaseModel):
    """Model for structure analysis response."""
    queries: List[QueryModel] = Field(..., min_length=6, max_length=15, description="List of queries to execute")
    chunk_structure: ChunkStructureModel = Field(..., description="Structure metadata for this chunk")
    explanation: Optional[str] = Field(None, description="Optional explanation of structure analysis")
    
    model_config = ConfigDict(
        extra='forbid',
        str_strip_whitespace=True
    )


class KBQueryResult(BaseModel):
    """Model for KB query result."""
    query_id: int = Field(..., description="Query identifier")
    query: str = Field(..., description="Query string that was executed")
    results: List[Dict[str, Any]] = Field(default_factory=list, description="Query results from knowledge base")
    success: bool = Field(..., description="Whether query executed successfully")
    error: Optional[str] = Field(None, description="Error message if query failed")
    
    model_config = ConfigDict(
        extra='forbid',
        str_strip_whitespace=True
    )


class ConflictModel(BaseModel):
    """Model for conflict detection - matches existing tools.py ConflictModel structure."""
    clarification_id: str = Field(..., description="Vendor's ID or Additional-[#] for other findings")
    vendor_quote: str = Field(..., description="Exact text verbatim OR 'N/A - Missing provision' for omissions")
    summary: str = Field(..., description="20-40 word context")
    source_doc: str = Field(..., description="KB document name (REQUIRED - must be an actual document from knowledge base)")
    clause_ref: str = Field(default="N/A", description="Specific section or 'N/A' if not applicable")
    conflict_type: str = Field(..., description="adds/deletes/modifies/contradicts/omits required/reverses obligation")
    rationale: str = Field(..., description="≤50 words on legal impact")
    
    @field_validator('source_doc')
    @classmethod
    def validate_source_doc(cls, v: str) -> str:
        """Ensure source_doc is not empty or invalid."""
        v_str = str(v).strip()
        if not v_str or v_str.lower() in ['n/a', 'na', 'none', 'unknown']:
            raise ValueError(f"source_doc must be a valid document name, got: '{v}'")
        return v_str
    
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
        extra='forbid',
        str_strip_whitespace=True
    )


class ConflictDetectionOutput(BaseModel):
    """Model for conflict detection response."""
    explanation: str = Field(..., description="Justification/explanation for conflict detection")
    conflicts: List[ConflictModel] = Field(default_factory=list, description="List of detected conflicts")
    
    model_config = ConfigDict(
        extra='forbid',
        str_strip_whitespace=True
    )


class JobInitializationOutput(BaseModel):
    """Model for job initialization result."""
    job_id: str = Field(..., description="Unique job identifier")
    status: str = Field(..., description="Job status")
    created_at: str = Field(..., description="ISO timestamp of job creation")
    
    model_config = ConfigDict(
        extra='forbid',
        str_strip_whitespace=True
    )


class DocumentSplitOutput(BaseModel):
    """Model for document split result."""
    chunk_count: int = Field(..., ge=1, description="Number of chunks created")
    chunks: List[Dict[str, Any]] = Field(..., description="List of chunk metadata with chunk_num, start_char, end_char")
    
    model_config = ConfigDict(
        extra='forbid',
        str_strip_whitespace=True
    )


class RedlineOutput(BaseModel):
    """Model for redline generation result."""
    success: bool = Field(..., description="Whether redlining succeeded")
    redlined_document_s3_key: Optional[str] = Field(None, description="S3 key of redlined document")
    error: Optional[str] = Field(None, description="Error message if redlining failed")
    
    model_config = ConfigDict(
        extra='forbid',
        str_strip_whitespace=True
    )


class SaveResultsOutput(BaseModel):
    """Model for save results."""
    success: bool = Field(..., description="Whether save succeeded")
    analysis_id: str = Field(..., description="Analysis identifier")
    error: Optional[str] = Field(None, description="Error message if save failed")
    
    model_config = ConfigDict(
        extra='forbid',
        str_strip_whitespace=True
    )


class CleanupOutput(BaseModel):
    """Model for cleanup result."""
    success: bool = Field(..., description="Whether cleanup succeeded")
    message: str = Field(..., description="Cleanup status message")
    
    model_config = ConfigDict(
        extra='forbid',
        str_strip_whitespace=True
    )


class ErrorOutput(BaseModel):
    """Model for error handling."""
    error: str = Field(..., description="Error message")
    error_type: str = Field(..., description="Type of error")
    timestamp: str = Field(..., description="ISO timestamp of error")
    
    model_config = ConfigDict(
        extra='forbid',
        str_strip_whitespace=True
    )

