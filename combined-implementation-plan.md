<!-- Combined Implementation Plan: Character-Based Chunking + Step Functions Migration -->
# Combined Implementation Plan: Character-Based Chunking & Step Functions Migration

## Overview

This plan combines two major initiatives:
1. **Character-Based Chunking**: Replace paragraph-based chunking with character-based chunking, add configuration for chunk size, and track chunks by number.
2. **Step Functions Migration with Dual-Track**: Create new Step Functions-based workflow alongside existing single Lambda workflow. All existing code remains untouched. New resources use `-v2` or `-stepfunctions` suffix. Enable via `USE_STEP_FUNCTIONS` environment variable. All Claude model outputs must be validated with strict Pydantic models.

## Phase 1: Configuration & Chunking System

### 1.1 Add Chunk Size Configuration

**Location**: `constants.py`

**Add new config variables:**

```python
# Document Chunking Configuration
# Character-based chunking configuration
CHUNK_SIZE_CHARACTERS = 100000  # Default chunk size in characters
CHUNK_OVERLAP_CHARACTERS = 5000  # Overlap between chunks
```

### 1.2 Character-Based Chunking Function

**Location**: `one_l/agent_api/agent/model.py` - Replace `_split_document_into_chunks`

**New function**: `_split_document_into_chunks(doc, chunk_size_characters=100000, chunk_overlap_characters=5000, is_pdf=False, pdf_bytes=None)`

**For PDFs:**
- Extract text using PyMuPDF
- Chunk by characters
- Calculate total characters
- Create chunks:
  - Chunk 1: characters 0 to CHUNK_SIZE_CHARACTERS
  - Chunk 2: characters (CHUNK_SIZE_CHARACTERS - CHUNK_OVERLAP_CHARACTERS) to (2 * CHUNK_SIZE_CHARACTERS - CHUNK_OVERLAP_CHARACTERS)
  - Continue until document is fully covered
- Create chunk documents (DOCX or PDF) containing only that chunk's content

**For DOCX:**
- Extract full document text
- Chunk by characters (same algorithm as PDF)
- Track character ranges
- Create chunk documents

**Chunk metadata structure:**

```python
{
    'bytes': chunk_bytes,
    'chunk_num': 0,  # Sequential chunk number (0-indexed or 1-indexed)
    'start_char': 0,  # Starting character index
    'end_char': 100000,  # Ending character index
    'is_pdf': False
}
```

**Key changes:**
- Remove paragraph-based logic
- Use character-based chunking as primary method
- Pass chunk context to AI model in prompts

### 1.3 Update Chunk Processing Logic

**Location**: `one_l/agent_api/agent/model.py` - Update `_review_document_chunked`

**Changes:**
- Use new character-based `_split_document_into_chunks` function
- Pass chunk context to Claude: "You are analyzing chunk X of Y (characters A-B)"

### 1.4 PDF Processing Updates

**Location**: `one_l/agent_api/agent/pdf_processor.py`

**Changes:**
- Ensure `extract_text_with_positions()` works correctly
- Update chunking to use character-based approach

## Phase 2: Pydantic Models & Prompt Engineering

### 2.1 Create Pydantic Models File

**Location**: `one_l/agent_api/agent/prompts/models.py` (new file)

**Models to create:**

- `QueryModel` - For individual KB query
  - query: str (required, min_length=10)
  - section: Optional[str]
  - max_results: int (default=50, ge=1, le=100)
  - query_id: Optional[int]

- `ChunkStructureModel` - For chunk structure metadata
  - sections: List[str]
  - vendor_exceptions: List[Dict[str, Any]]
  - document_references: List[str]
  - character_range: str  # e.g., "characters 0-100000"

- `StructureAnalysisOutput` - For structure analysis response
  - queries: List[QueryModel] (min_length=6, max_length=15)
  - chunk_structure: ChunkStructureModel
  - explanation: Optional[str]

- `KBQueryResult` - For KB query result
  - query_id: int
  - query: str
  - results: List[Dict[str, Any]]
  - success: bool
  - error: Optional[str]

- `ConflictModel` - Use existing from tools.py as base
  - clarification_id: str
  - vendor_quote: str
  - summary: str
  - source_doc: str
  - conflict_type: str
  - severity: str
  - recommendation: str

- `ConflictDetectionOutput` - For conflict detection response
  - explanation: str (required)
  - conflicts: List[ConflictModel]

- `JobInitializationOutput` - For job initialization
  - job_id: str
  - status: str
  - created_at: str

- `DocumentSplitOutput` - For document split result
  - chunk_count: int
  - chunks: List[Dict[str, Any]]  # Each with chunk_num, start_char, end_char

- `RedlineOutput` - For redline generation result
  - success: bool
  - redlined_document_s3_key: Optional[str]
  - error: Optional[str]

- `SaveResultsOutput` - For save results
  - success: bool
  - analysis_id: str
  - error: Optional[str]

- `CleanupOutput` - For cleanup result
  - success: bool
  - message: str

- `ErrorOutput` - For error handling
  - error: str
  - error_type: str
  - timestamp: str

**All models must:**
- Use `extra='forbid'` to reject extra fields
- Use `str_strip_whitespace=True` for auto-stripping
- Include field validators where needed
- Include comprehensive Field descriptions

### 2.2 Create New Prompt Files

**Location**: `one_l/agent_api/agent/prompts/` (new directory)

**Files to create:**
- `structure_analysis_prompt.py` - For AnalyzeChunkStructure Lambda
- `conflict_detection_prompt.py` - For AnalyzeChunkWithKB Lambda

**Prompt Split Strategy (from AI prompt engineering perspective):**

**Structure Analysis Prompt** (extract from original lines 1-6, 8-12, 16-117):
- **CRITICAL OUTPUT REQUIREMENT**: Must output JSON matching StructureAnalysisOutput Pydantic model exactly
- **CHUNK CONTEXT**: Include chunk context (e.g., "You are analyzing chunk 1 of 5 (characters 0-100000)")
- Document structure analysis methodology (STEP 1)
- Adaptive zone mapping guidance
- Documents to check against list
- Query construction principles (STEP 2)
- 6-12 queries requirement with verification checklist
- Query construction principles (group related, include context, cast wide nets, be exhaustive, adapt to structure)
- **JSON Schema**: Include exact JSON schema in prompt matching Pydantic model

**Conflict Detection Prompt** (extract from original lines 1-6, 118-160, 162-209, 210-257):
- **CRITICAL OUTPUT REQUIREMENT**: Must output JSON matching ConflictDetectionOutput Pydantic model exactly
- **CHUNK CONTEXT**: Include chunk context (e.g., "You are analyzing chunk 1 of 5 (characters 0-100000)")
- Comprehensive conflict detection (STEP 3)
- Pattern recognition guidance
- Systematic verification (STEP 4)
- Field specifications (all conflict fields)
- Source doc requirements
- All output format requirements
- **JSON Schema**: Include exact JSON schema in prompt matching Pydantic model

**Key Principles:**
- Both prompts retain critical guidance from original
- Add explicit JSON schema requirements matching Pydantic models
- Emphasize strict adherence to schema
- Include examples of valid JSON output

### 2.3 Update Model Instructions

**Location**: `one_l/agent_api/agent/model.py`

**Changes:**
- Update chunk analysis instructions to include:
  - Chunk number context ("You are analyzing chunk X of Y")
  - Character range context ("characters A-B")

### 2.4 Update System Prompt

**Location**: `one_l/agent_api/agent/system_prompt.py`

**Changes:**
- Note: Original prompt remains intact for existing workflow

### 2.5 Enhance Tool Descriptions

**Location**: `one_l/agent_api/agent/tools.py` - Update `get_tool_definitions()`

**Enhancements for `retrieve_from_knowledge_base` tool:**

- Expand description with examples of good queries
- Add guidance on when to use the tool
- Include information about query construction best practices
- Specify expected output format
- Add examples of effective queries
- Explain max_results parameter usage

**Example enhanced description:**

```python
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
"""
```

### 2.6 Keep Original Prompt Intact

- `one_l/agent_api/agent/system_prompt.py` - No changes to existing functionality
- Existing code continues using `SYSTEM_PROMPT`
- New code imports from new prompt files

## Phase 3: Conflict Parsing & Redlining Updates

### 3.1 Update Conflict Parsing

**Location**: `one_l/agent_api/agent/tools.py`

**Update `parse_conflicts_for_redlining()` to:**
- Extract conflict data from conflict objects
- Process conflicts for redlining based on character positions

### 3.2 Update Redlining Functions

**Location**: `one_l/agent_api/agent/tools.py`

**Update `apply_exact_sentence_redlining()` to:**
- Use character positions from conflicts for redlining
- Process conflicts based on character ranges

**Update `_redline_pdf_document()` to:**
- Use character positions from conflicts for redlining
- Process conflicts based on character ranges

## Phase 4: New Lambda Functions with Pydantic Validation

### 4.1 Create Lambda Function Directory Structure

**Location**: `one_l/agent_api/functions/stepfunctions/` (new directory)

**New Lambda functions:**

1. `initialize_job/` - Initialize job, save status
2. `split_document/` - Split document into chunks (using character-based chunking)
3. `analyze_chunk_structure/` - Analyze chunk structure, generate queries
4. `analyze_document_structure/` - For single document (non-chunked)
5. `retrieve_kb_query/` - Execute single KB query
6. `analyze_chunk_with_kb/` - Analyze chunk with KB results
7. `analyze_document_with_kb/` - Analyze single document with KB results
8. `merge_chunk_results/` - Merge all chunk results
9. `generate_redline/` - Create redlined document
10. `save_results/` - Save to DynamoDB
11. `cleanup_session/` - Clean up temporary files
12. `handle_error/` - Handle errors, update status

### 4.2 Lambda Function Implementation with Pydantic

**All Lambda functions must:**
- Import Pydantic models from `agent_api.agent.prompts.models`
- Validate all Claude responses with Pydantic models
- Return validated JSON (via `model_dump_json()`)
- Handle ValidationError gracefully
- Log validation errors with details

**Example structure for `analyze_chunk_structure/lambda_function.py`:**

```python
from agent_api.agent.prompts.structure_analysis_prompt import STRUCTURE_ANALYSIS_PROMPT
from agent_api.agent.prompts.models import StructureAnalysisOutput, ValidationError
from agent_api.agent.model import Model, _extract_and_log_thinking, _extract_json_only
import boto3
import json
import logging

logger = logging.getLogger()

def lambda_handler(event, context):
    try:
        # Load chunk from S3
        chunk_s3_key = event['chunk_s3_key']
        # ... load chunk ...
        
        # Call Claude with STRUCTURE_ANALYSIS_PROMPT
        model = Model(knowledge_base_id, region)
        response = model._call_claude_with_tools(messages)
        
        # Extract JSON
        response_json = _extract_json_only(response_content)
        
        # Validate with Pydantic
        try:
            validated_output = StructureAnalysisOutput.model_validate_json(response_json)
            logger.info(f"Pydantic validation successful: {len(validated_output.queries)} queries")
        except ValidationError as e:
            logger.error(f"Pydantic validation failed: {e.errors()}")
            # Retry or fail gracefully
            raise
        
        # Return validated output
        return {
            "statusCode": 200,
            "body": validated_output.model_dump_json()
        }
    except Exception as e:
        logger.error(f"Error in analyze_chunk_structure: {e}")
        raise
```

### 4.3 Lambda Function Details with Validation

**initialize_job:**
- Creates job_id
- Saves initial status to DynamoDB
- Returns `JobInitializationOutput` (validated)

**split_document:**
- Uses character-based `_split_document_into_chunks` from `model.py`
- Saves chunks to S3
- Returns `DocumentSplitOutput` (validated) with chunk metadata

**analyze_chunk_structure:**
- Loads chunk from S3
- Calls Claude with STRUCTURE_ANALYSIS_PROMPT
- **Validates response with `StructureAnalysisOutput` Pydantic model**
- Returns validated queries array

**analyze_document_structure:**
- Same as analyze_chunk_structure for single document
- **Validates response with `StructureAnalysisOutput` Pydantic model**

**retrieve_kb_query:**
- Wraps existing `retrieve_from_knowledge_base` function
- Executes single query
- Returns `KBQueryResult` (validated)

**analyze_chunk_with_kb:**
- Loads chunk from S3
- Calls Claude with CONFLICT_DETECTION_PROMPT
- Provides KB results as context
- **Validates response with `ConflictDetectionOutput` Pydantic model**
- Returns validated conflicts JSON

**analyze_document_with_kb:**
- Same as analyze_chunk_with_kb for single document
- **Validates response with `ConflictDetectionOutput` Pydantic model**

**merge_chunk_results:**
- Merges conflicts from all chunks
- Renumbers Additional-[#] conflicts
- Deduplicates conflicts
- **Validates merged output with `ConflictDetectionOutput` Pydantic model**

**generate_redline:**
- Wraps existing `redline_document` function
- Uses character positions from conflicts
- Returns `RedlineOutput` (validated)

**save_results:**
- Wraps existing `save_analysis_to_dynamodb` function
- Returns `SaveResultsOutput` (validated)

**cleanup_session:**
- Uses existing `_cleanup_session_documents` function
- Returns `CleanupOutput` (validated)

**handle_error:**
- Updates job status to "failed"
- Returns `ErrorOutput` (validated)

## Phase 5: Step Functions State Machine

### 5.1 Create Step Functions Construct

**Location**: `one_l/agent_api/functions/stepfunctions/stepfunctions_construct.py` (new file)

**CDK Construct:**
- Creates Step Functions state machine
- Defines all states with proper error handling
- Configures retry policies, timeouts
- Uses all new Lambda functions

### 5.2 State Machine Definition

**Key states:**
- InitializeJob → SplitDocument → CheckChunkCount
- If chunks > 1: AnalyzeChunksParallel (Map) → MergeChunkResults
- If chunks = 1: AnalyzeDocumentStructure → RetrieveKBQueriesParallel → AnalyzeDocumentWithKB
- GenerateRedline → SaveResults → CleanupSession

**Parallel execution:**
- Map state for chunk analysis (MaxConcurrency: 10)
- Map state for KB queries (MaxConcurrency: 20)
- Nested Map states for chunk → queries → analysis

## Phase 6: CDK Infrastructure Updates

### 6.1 Create New CDK Construct

**Location**: `one_l/agent_api/functions/stepfunctions/stepfunctions.py` (new file)

**Creates:**
- All new Lambda functions (with `-v2` suffix)
- Step Functions state machine
- IAM roles and permissions
- Environment variables (including `USE_STEP_FUNCTIONS` flag)

### 6.2 Update Agent Construct

**Location**: `one_l/agent_api/functions/agent/agent.py`

**Changes:**
- Add optional Step Functions construct
- Conditionally create based on feature flag
- Export Step Functions ARN

### 6.3 API Gateway Integration

**Location**: `one_l/api_gateway/api_gateway.py`

**Changes:**
- Check `USE_STEP_FUNCTIONS` environment variable
- If enabled: Start Step Functions execution, return execution ARN
- If disabled: Use existing Lambda invocation

## Phase 7: Frontend Updates (Optional - Feature Flagged)

### 7.1 Update API Service

**Location**: `one_l/user_interface/src/services/api.js`

**Changes:**
- Add Step Functions polling method
- Check for execution ARN in response

### 7.2 Update App Component

**Location**: `one_l/user_interface/src/App.js`

**Changes:**
- Poll Step Functions execution status when execution ARN present
- On completion, fetch results from DynamoDB
- Show progress based on execution history

## Implementation Details

### Character-Based Chunking Algorithm

1. Extract full document text
2. Calculate total characters
3. Create chunks:
   - Chunk 1: characters 0 to CHUNK_SIZE_CHARACTERS
   - Chunk 2: characters (CHUNK_SIZE_CHARACTERS - CHUNK_OVERLAP_CHARACTERS) to (2 * CHUNK_SIZE_CHARACTERS - CHUNK_OVERLAP_CHARACTERS)
   - Continue until document is fully covered
4. Create chunk documents (DOCX or PDF) containing only that chunk's content

### Chunk Context in Instructions

When analyzing a chunk, include:
- "You are analyzing chunk X of Y (characters A-B)"

## File Structure Summary

### New Files to Create:

```
one_l/agent_api/agent/prompts/
  ├── __init__.py
  ├── models.py (Pydantic models for all outputs)
  ├── structure_analysis_prompt.py
  └── conflict_detection_prompt.py

one_l/agent_api/functions/stepfunctions/
  ├── __init__.py
  ├── stepfunctions.py (CDK construct)
  ├── stepfunctions_construct.py (state machine)
  ├── initialize_job/lambda_function.py
  ├── split_document/lambda_function.py
  ├── analyze_chunk_structure/lambda_function.py
  ├── analyze_document_structure/lambda_function.py
  ├── retrieve_kb_query/lambda_function.py
  ├── analyze_chunk_with_kb/lambda_function.py
  ├── analyze_document_with_kb/lambda_function.py
  ├── merge_chunk_results/lambda_function.py
  ├── generate_redline/lambda_function.py
  ├── save_results/lambda_function.py
  ├── cleanup_session/lambda_function.py
  └── handle_error/lambda_function.py
```

### Files to Modify:

- `constants.py` - Add chunk size configuration
- `one_l/agent_api/agent/model.py` - Replace chunking function, update instructions
- `one_l/agent_api/agent/system_prompt.py` - Keep original intact
- `one_l/agent_api/agent/tools.py` - Update conflict parsing and redlining, enhance tool descriptions
- `one_l/agent_api/agent/pdf_processor.py` - Ensure text extraction works with character chunks
- `one_l/agent_api/functions/agent/agent.py` - Add Step Functions construct
- `one_l/api_gateway/api_gateway.py` - Add feature flag check
- `one_l/user_interface/src/services/api.js` - Add Step Functions polling (optional)
- `one_l/user_interface/src/App.js` - Add Step Functions polling (optional)

### Files to Keep Intact:

- All existing files remain unchanged (dual-track approach)

## Testing Considerations

- Verify chunks are created correctly by character count
- Verify redlining uses character positions correctly
- Test with both PDF and DOCX documents
- Verify overlap works correctly at chunk boundaries
- Test both workflows: existing Lambda workflow (flag OFF) and new Step Functions workflow (flag ON)
- Compare results and performance
- Validate no regressions
- Test Pydantic validation catches invalid responses
- Test error handling and retry logic

## Implementation Order

1. **Phase 1**: Configuration & Chunking System
   - Add CHUNK_SIZE_CHARACTERS and CHUNK_OVERLAP_CHARACTERS to constants.py
   - Replace _split_document_into_chunks() with character-based chunking
   - Update PDF processing to ensure text extraction works

2. **Phase 2**: Pydantic Models & Prompt Engineering
   - Create Pydantic models file (models.py)
   - Create prompt files with JSON schema requirements
   - Enhance tool descriptions
   - Update system prompt (keep original intact)
   - Update model instructions

3. **Phase 3**: Conflict Parsing & Redlining Updates
   - Update parse_conflicts_for_redlining() to extract conflict data
   - Update redlining functions to use character positions from conflicts

4. **Phase 4**: New Lambda Functions with Pydantic Validation
   - Create all 12 new Lambda function directories and stub implementations
   - Implement each Lambda with Pydantic validation
   - Test individual Lambda functions

5. **Phase 5**: Step Functions State Machine
   - Create Step Functions CDK construct
   - Define state machine with all states, retry policies, timeouts, error handling
   - Use Map states for parallel execution

6. **Phase 6**: CDK Infrastructure Updates
   - Update agent construct to optionally create Step Functions construct
   - Update API Gateway to check USE_STEP_FUNCTIONS environment variable

7. **Phase 7**: Frontend Updates (Optional)
   - Update API service to add Step Functions polling
   - Update App component to poll Step Functions execution status

8. **Testing & Validation**
   - Test both workflows
   - Compare results and performance
   - Validate no regressions

## To-dos

### Phase 1: Configuration & Chunking
- [ ] Add CHUNK_SIZE_CHARACTERS and CHUNK_OVERLAP_CHARACTERS to constants.py
- [ ] Replace _split_document_into_chunks() with character-based chunking
- [ ] Ensure extract_text_with_positions() works correctly in pdf_processor.py
- [ ] Update chunk processing logic to pass chunk context to Claude

### Phase 2: Pydantic Models & Prompts
- [ ] Create new prompt files: structure_analysis_prompt.py and conflict_detection_prompt.py in one_l/agent_api/agent/prompts/ directory
- [ ] Create Pydantic models file (models.py) with all output models
- [ ] Update chunk analysis instructions to include chunk context
- [ ] Enhance tool descriptions in tools.py

### Phase 3: Conflict Parsing & Redlining
- [ ] Update parse_conflicts_for_redlining() to extract conflict data
- [ ] Update redlining functions to use character positions from conflicts

### Phase 4: Lambda Functions
- [ ] Create all 12 new Lambda function directories and stub implementations in one_l/agent_api/functions/stepfunctions/
- [ ] Implement initialize_job Lambda - creates job_id, saves initial status to DynamoDB
- [ ] Implement split_document Lambda - uses character-based _split_document_into_chunks, saves chunks to S3
- [ ] Implement analyze_chunk_structure Lambda - loads chunk, calls Claude with STRUCTURE_ANALYSIS_PROMPT, validates with Pydantic
- [ ] Implement analyze_document_structure Lambda - same as chunk structure for single document
- [ ] Implement retrieve_kb_query Lambda - wraps existing retrieve_from_knowledge_base function
- [ ] Implement analyze_chunk_with_kb Lambda - loads chunk, calls Claude with CONFLICT_DETECTION_PROMPT, validates with Pydantic
- [ ] Implement analyze_document_with_kb Lambda - same as chunk with KB for single document
- [ ] Implement merge_chunk_results Lambda - merges conflicts, renumbers, deduplicates, validates with Pydantic
- [ ] Implement generate_redline Lambda - wraps redline_document, uses character positions
- [ ] Implement save_results Lambda - wraps save_analysis_to_dynamodb
- [ ] Implement cleanup_session Lambda - wraps _cleanup_session_documents
- [ ] Implement handle_error Lambda - updates job status to failed

### Phase 5: Step Functions
- [ ] Create Step Functions CDK construct in one_l/agent_api/functions/stepfunctions/stepfunctions.py
- [ ] Define state machine with all states, retry policies, timeouts, error handling
- [ ] Use Map states for parallel execution

### Phase 6: CDK Infrastructure
- [ ] Update one_l/agent_api/functions/agent/agent.py to optionally create Step Functions construct based on feature flag
- [ ] Update one_l/api_gateway/api_gateway.py to check USE_STEP_FUNCTIONS environment variable

### Phase 7: Frontend (Optional)
- [ ] Update one_l/user_interface/src/services/api.js to add Step Functions polling method
- [ ] Update one_l/user_interface/src/App.js to poll Step Functions execution status

### Testing
- [ ] Test both workflows: existing Lambda workflow (flag OFF) and new Step Functions workflow (flag ON)
- [ ] Compare results and performance
- [ ] Validate no regressions
- [ ] Test Pydantic validation
- [ ] Test error handling and retry logic

