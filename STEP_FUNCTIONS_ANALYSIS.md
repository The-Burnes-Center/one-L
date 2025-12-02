# Step Functions Workflow Analysis

## Overview

The Step Functions workflow orchestrates a document review process that analyzes legal documents for conflicts against a knowledge base. The workflow handles both single documents and chunked documents (for large files), processes them in parallel, and generates redlined documents with conflict annotations.

**State Machine Name**: `{stack-name}-document-review`  
**Timeout**: 2 hours  
**Logging**: All events logged to CloudWatch Logs (1 week retention)

---

## Architecture

### Entry Points

1. **Start Workflow Lambda** (`start_workflow`)
   - Entry point from API Gateway
   - Generates `job_id` upfront
   - Starts Step Functions execution asynchronously
   - Returns `job_id` immediately for frontend polling

2. **Job Status Lambda** (`job_status`)
   - Provides status polling endpoint
   - Reads from DynamoDB and Step Functions execution status
   - Returns progress, stage, and status

### State Machine Workflow

```
InitializeJob → SplitDocument → AnalyzeChunksParallel (Map) → StoreChunkAnalyses → MergeChunkResults → GenerateRedline → SaveResults → CleanupSession
```

**Key Design Decision**: The workflow uses a unified Map state for both single and chunked documents. Single documents are treated as 1-chunk arrays, simplifying the workflow.

---

## State-by-State Analysis

### 1. InitializeJob

**Lambda**: `initialize_job`  
**Timeout**: 30 seconds  
**Memory**: 2048 MB

**Purpose**: Initialize the job in DynamoDB and resolve bucket names

**Input**: Full context from `start_workflow`
- `job_id`, `timestamp`, `session_id`, `user_id`
- `document_s3_key`, `bucket_type`
- `terms_profile`, `knowledge_base_id`, `region`

**Output**: Merged at root (`$.Payload`)
- All input fields preserved
- `bucket_name` (resolved from `bucket_type`)
- `status: "processing"`

**Retry Policy**:
- Errors: `TIMEOUT`, `TASKS_FAILED`
- Interval: 2 seconds
- Max Attempts: 3
- Backoff Rate: 2.0

**Error Handling**: Catches all errors → `HandleError`

---

### 2. SplitDocument

**Lambda**: `split_document`  
**Timeout**: 5 minutes  
**Memory**: 2048 MB

**Purpose**: Split large documents into chunks for parallel processing

**Input**: Full context from `initialize_job`

**Output**: Merged at `$.split_result`
- `chunk_count`: int
- `chunks`: Array of `{chunk_num, start_char, end_char, s3_key}`
- `bucket_name`: string

**Retry Policy**:
- Errors: `TIMEOUT`, `TASKS_FAILED`
- Interval: 2 seconds
- Max Attempts: 2
- Backoff Rate: 2.0

**Error Handling**: Catches all errors → `HandleError`

**Note**: Always creates at least 1 chunk (even for single documents)

---

### 3. AnalyzeChunksParallel (Map State)

**Type**: `sfn.Map`  
**Items Path**: `$.split_result.chunks`  
**Max Concurrency**: 10  
**Result Path**: `$.chunk_analyses`

**Purpose**: Process all chunks in parallel using unified workflow

**Item Selector** (creates input for each iteration):
```json
{
  "chunk_s3_key": "$.Map.Item.Value.s3_key",
  "chunk_num": "$.Map.Item.Value.chunk_num",
  "start_char": "$.Map.Item.Value.start_char",
  "end_char": "$.Map.Item.Value.end_char",
  "bucket_name": "$.split_result.bucket_name",
  "total_chunks": "$.split_result.chunk_count",
  "job_id": "$.job_id",
  "session_id": "$.session_id",
  "user_id": "$.user_id",
  "document_s3_key": "$.document_s3_key",
  "terms_profile": "$.terms_profile",
  "knowledge_base_id": "$.knowledge_base_id",
  "region": "$.region",
  "timestamp": "$.timestamp"
}
```

**Item Processor**: Unified workflow chain (see below)

---

### 4. Unified Workflow (Inside Map)

Each chunk goes through this sequence:

#### 4.1 AnalyzeStructure

**Lambda**: `analyze_structure`  
**Timeout**: 15 minutes  
**Memory**: 2048 MB

**Purpose**: Analyze document structure and extract queries for knowledge base lookup

**Input**:
- `chunk_s3_key` or `document_s3_key` (fallback)
- `bucket_name`, `knowledge_base_id`, `region`
- `chunk_num`, `total_chunks`, `start_char`, `end_char`
- Context: `job_id`, `session_id`, `timestamp`

**Output**: `$.structure_result`
- `structure_s3_key`: S3 reference (always stored in S3)
- `queries`: Array of query objects

**Retry Policy**:
- Errors: `TIMEOUT`, `TASKS_FAILED`
- Interval: 2 seconds
- Max Attempts: 2
- Backoff Rate: 2.0

**Design**: Always stores results in S3 to avoid Step Functions payload size limits

---

#### 4.2 RetrieveAllKBQueries

**Lambda**: `retrieve_all_kb_queries`  
**Timeout**: 5 minutes  
**Memory**: 2048 MB

**Purpose**: Retrieve all knowledge base results for queries in a single Lambda call

**Input**:
- `structure_s3_key`: Load structure results from S3
- `knowledge_base_id`, `region`
- `job_id`, `session_id`, `bucket_name`

**Output**: `$.kb_retrieval_result`
- `results_s3_key`: S3 reference to aggregated KB results
- `results_count`, `queries_count`, `success_count`, `failed_count`

**Retry Policy**:
- Errors: `TIMEOUT`, `TASKS_FAILED`
- Interval: 2 seconds
- Max Attempts: 2
- Backoff Rate: 2.0

**Design**: Consolidates parallel KB queries into single Lambda call (replaces nested Map state)

---

#### 4.3 AnalyzeWithKB

**Lambda**: `analyze_with_kb`  
**Timeout**: 15 minutes  
**Memory**: 2048 MB

**Purpose**: Analyze chunk/document with knowledge base context to detect conflicts

**Input**:
- `chunk_s3_key` or `document_s3_key` (fallback)
- `bucket_name`, `knowledge_base_id`, `region`
- `kb_results_s3_key`: Load KB results from S3
- `chunk_num`, `total_chunks`, `start_char`, `end_char`
- Context: `job_id`, `session_id`, `timestamp`

**Output**: `$.analysis_result`
- `results_s3_key`: S3 reference to conflict detection results
- `chunk_num`, `conflicts_count`, `has_results`

**Retry Policy**:
- Errors: `TIMEOUT`, `TASKS_FAILED`
- Interval: 2 seconds
- Max Attempts: 2
- Backoff Rate: 2.0

**Design**: Always stores results in S3 (Map state collects references, not full data)

---

### 5. StoreChunkAnalyses

**Lambda**: `store_large_results`  
**Timeout**: 30 seconds  
**Memory**: 2048 MB

**Purpose**: Store aggregated chunk analyses in S3 if they exceed Step Functions payload limits (256KB)

**Input**:
- `kb_results`: `$.chunk_analyses` (array of S3 references)
- `job_id`, `session_id`, `bucket_name`
- `storage_type`: "chunk_analyses"

**Output**: `$.chunk_storage`
- `s3_key`: S3 reference to aggregated data

**Retry Policy**:
- Errors: `TIMEOUT`, `TASKS_FAILED`
- Interval: 2 seconds
- Max Attempts: 2
- Backoff Rate: 2.0

**Critical**: Prevents Step Functions from failing due to large payloads

---

### 6. MergeChunkResults

**Lambda**: `merge_chunk_results`  
**Timeout**: 2 minutes  
**Memory**: 2048 MB

**Purpose**: Merge conflict detection results from all chunks

**Input**:
- `chunk_results`: `$.chunk_analyses` (array of S3 references)
- `chunk_analyses_s3_key`: `$.chunk_storage.s3_key` (backup)
- `bucket_name`, `job_id`, `timestamp`

**Output**: `$.conflicts_result`
- `conflicts_s3_key`: S3 reference to merged conflicts
- `conflicts`: Array of conflict objects (if small enough)
- `conflict_count`, `total_chunks`

**Retry Policy**:
- Errors: `TIMEOUT`, `TASKS_FAILED`
- Interval: 2 seconds
- Max Attempts: 2
- Backoff Rate: 2.0

**Design**: Loads each chunk result from S3, merges, deduplicates, renumbers conflicts

---

### 7. GenerateRedline

**Lambda**: `generate_redline`  
**Timeout**: 10 minutes  
**Memory**: 2048 MB

**Purpose**: Generate redlined document with conflict annotations

**Input**:
- `conflicts_s3_key`: Load conflicts from S3
- `conflicts_result`: Fallback for legacy support
- `document_s3_key`, `bucket_name`, `bucket_type`
- `session_id`, `user_id`, `job_id`, `timestamp`

**Output**: `$.redline_result`
- `redlined_document_s3_key`: S3 reference to redlined document
- `success`, `error`

**Retry Policy**:
- Errors: `TIMEOUT`, `TASKS_FAILED`
- Interval: 2 seconds
- Max Attempts: 2
- Backoff Rate: 2.0

**Error Handling**: Catches all errors → `HandleError`

---

### 8. SaveResults

**Lambda**: `save_results`  
**Timeout**: 30 seconds  
**Memory**: 2048 MB

**Purpose**: Save analysis results to DynamoDB

**Input**:
- `conflicts_s3_key`: Load conflicts from S3
- `analysis_json`: Fallback for legacy support
- `bucket_name`, `document_s3_key`, `bucket_type`
- `redlined_s3_key`: `$.redline_result.redlined_document_s3_key`
- `session_id`, `user_id`, `job_id`, `timestamp`

**Output**: `$.save_result`
- `success`, `analysis_id`, `error`

**Retry Policy**:
- Errors: `TIMEOUT`, `TASKS_FAILED`
- Interval: 2 seconds
- Max Attempts: 2
- Backoff Rate: 2.0

**Error Handling**: Catches all errors → `HandleError`

---

### 9. CleanupSession

**Lambda**: `cleanup_session`  
**Timeout**: 30 seconds  
**Memory**: 2048 MB

**Purpose**: Clean up temporary session documents

**Input**:
- `session_id`, `user_id` (from context)

**Output**: `$.cleanup_result`
- `success`, `message`

**Retry Policy**:
- Errors: `TIMEOUT`, `TASKS_FAILED`
- Interval: 2 seconds
- Max Attempts: 2
- Backoff Rate: 2.0

**Error Handling**: Catches all errors → `HandleError`

---

### 10. HandleError

**Lambda**: `handle_error`  
**Timeout**: 30 seconds  
**Memory**: 2048 MB

**Purpose**: Centralized error handler for all failed states

**Input**: Error context from catch blocks
- `job_id`, `timestamp`
- `error`: Error object with `Error` and `Cause`
- `error_type`: Optional error type

**Output**: `$.Payload`
- `error`, `error_type`, `timestamp`

**Actions**:
- Updates DynamoDB job status to "failed"
- Sets `stage` to "failed"
- Stores error message
- Sets `progress` to 0

---

## Data Flow Patterns

### Context Preservation

All context flows through the entire workflow:
- `job_id`, `session_id`, `user_id`
- `document_s3_key`, `bucket_type`, `bucket_name`
- `terms_profile`, `knowledge_base_id`, `region`
- `timestamp`

### S3 Storage Pattern

**Large Data Strategy**: All intermediate results stored in S3 to avoid Step Functions payload limits (256KB)

1. **Structure Analysis**: Always stored in S3 → `structure_s3_key`
2. **KB Results**: Always stored in S3 → `results_s3_key`
3. **Chunk Analysis**: Always stored in S3 → `results_s3_key` (per chunk)
4. **Merged Conflicts**: Stored in S3 → `conflicts_s3_key`
5. **Redlined Document**: Stored in S3 → `redlined_document_s3_key`

### Result Path Strategy

- `$.Payload`: Root-level merge (used by `initialize_job`)
- `$.split_result`: Split document results
- `$.structure_result`: Structure analysis results
- `$.kb_retrieval_result`: KB retrieval results
- `$.analysis_result`: Per-chunk analysis results
- `$.chunk_analyses`: Array of chunk analysis results (from Map)
- `$.chunk_storage`: Storage result for large chunk analyses
- `$.conflicts_result`: Merged conflicts
- `$.redline_result`: Redline generation results
- `$.save_result`: Save operation results
- `$.cleanup_result`: Cleanup operation results
- `$.error`: Error information (from catch blocks)

---

## Error Handling

### Retry Policies

**Standard Retry Configuration**:
- Errors: `TIMEOUT`, `TASKS_FAILED`
- Interval: 2 seconds
- Max Attempts: 2-3 (varies by state)
- Backoff Rate: 2.0

**Exceptions**:
- `InitializeJob`: 3 attempts (more critical)
- Other states: 2 attempts

### Catch Blocks

**States with Error Handling**:
- `InitializeJob` → `HandleError`
- `SplitDocument` → `HandleError`
- `GenerateRedline` → `HandleError`
- `SaveResults` → `HandleError`
- `CleanupSession` → `HandleError`

**Note**: Map state errors propagate to parent state (no catch blocks inside Map)

### Error Handler

`HandleError` Lambda:
- Updates DynamoDB with failed status
- Preserves error details
- Returns error output for Step Functions

---

## Performance Considerations

### Parallelization

1. **Chunk Processing**: Up to 10 chunks processed in parallel (`max_concurrency=10`)
2. **Unified Workflow**: Each chunk goes through structure → KB → analysis sequentially
3. **KB Queries**: Consolidated into single Lambda call (replaces parallel Map)

### Timeouts

- **Total Workflow**: 2 hours
- **Longest Lambda**: 15 minutes (`analyze_structure`, `analyze_with_kb`)
- **Shortest Lambda**: 10 seconds (`job_status`)

### Memory Allocation

- **Standard**: 2048 MB (most Lambda functions)
- **Lightweight**: 512 MB (`start_workflow`), 256 MB (`job_status`)

### Payload Size Management

- **Step Functions Limit**: 256KB per state
- **Mitigation**: All large results stored in S3
- **Storage Check**: `StoreChunkAnalyses` handles Map state aggregation

---

## Lambda Functions Summary

| Function | Handler | Timeout | Memory | Purpose |
|----------|---------|---------|--------|---------|
| `start_workflow` | `start_workflow/lambda_function.lambda_handler` | 30s | 512 MB | Start workflow, return job_id |
| `job_status` | `job_status/lambda_function.lambda_handler` | 10s | 256 MB | Poll job status |
| `initialize_job` | `initialize_job/lambda_function.lambda_handler` | 30s | 2048 MB | Initialize job in DynamoDB |
| `split_document` | `split_document/lambda_function.lambda_handler` | 5m | 2048 MB | Split document into chunks |
| `analyze_structure` | `analyze_structure/lambda_function.lambda_handler` | 15m | 2048 MB | Analyze structure, extract queries |
| `retrieve_all_kb_queries` | `retrieve_all_kb_queries/lambda_function.lambda_handler` | 5m | 2048 MB | Retrieve KB results for all queries |
| `analyze_with_kb` | `analyze_with_kb/lambda_function.lambda_handler` | 15m | 2048 MB | Detect conflicts with KB context |
| `store_large_results` | `store_large_results/lambda_function.lambda_handler` | 30s | 2048 MB | Store large results in S3 |
| `merge_chunk_results` | `merge_chunk_results/lambda_function.lambda_handler` | 2m | 2048 MB | Merge chunk conflict results |
| `generate_redline` | `generate_redline/lambda_function.lambda_handler` | 10m | 2048 MB | Generate redlined document |
| `save_results` | `save_results/lambda_function.lambda_handler` | 30s | 2048 MB | Save results to DynamoDB |
| `cleanup_session` | `cleanup_session/lambda_function.lambda_handler` | 30s | 2048 MB | Clean up session documents |
| `handle_error` | `handle_error/lambda_function.lambda_handler` | 30s | 2048 MB | Handle errors, update status |

---

## IAM Permissions

### Step Functions Execution Role

- Start Step Functions execution
- Invoke all Lambda functions
- Write to CloudWatch Logs

### Lambda Function Roles

**Common Permissions** (via `create_agent_role`):
- Read/write to S3 buckets (knowledge, user documents, agent processing)
- Read/write to DynamoDB (analyses table)
- Access OpenSearch Serverless collection
- Invoke Bedrock models
- Write to CloudWatch Logs

**Specific Permissions**:
- `start_workflow`: Start Step Functions execution, update sessions table
- `job_status`: Read DynamoDB, describe Step Functions executions

---

## Issues and Recommendations

### ✅ Strengths

1. **Unified Workflow**: Single Map state handles both single and chunked documents
2. **S3 Storage**: Proper handling of large payloads via S3 references
3. **Error Handling**: Comprehensive retry policies and catch blocks
4. **Parallelization**: Efficient parallel processing of chunks
5. **Progress Tracking**: Progress updates via DynamoDB for frontend polling

### ⚠️ Potential Issues

1. **Missing Error Handling**: ~~Some states (Map, intermediate states) don't have catch blocks~~ ✅ **FIXED**
   - **Status**: All states now have catch blocks attached to HandleError Lambda
   - **Fixed States**: `analyze_structure`, `retrieve_all_kb_queries`, `analyze_with_kb`, `store_chunk_analyses`, `merge_chunk_results`

2. **Payload Size**: Map state aggregation could exceed 256KB
   - **Mitigation**: `StoreChunkAnalyses` handles this
   - **Status**: ✅ Addressed

3. **Timeout Risk**: 15-minute Lambdas may timeout on very large documents
   - **Mitigation**: Document splitting handles this
   - **Recommendation**: Monitor timeout rates

4. **Concurrency Limits**: Max 10 concurrent chunk processing
   - **Impact**: May be slow for documents with many chunks
   - **Recommendation**: Consider increasing if needed (monitor costs)

5. **Error Handler Dependency**: If `HandleError` fails, no recovery
   - **Impact**: Failed jobs may not be properly marked
   - **Recommendation**: Add fallback error handling or dead-letter queue

### 🔧 Recommendations

1. ✅ **Error Handling**: All states now have catch blocks attached to HandleError Lambda
2. **Add Dead-Letter Queue**: For failed executions that can't be handled
3. **Increase Monitoring**: Add CloudWatch alarms for:
   - Failed executions
   - Timeout rates
   - Long-running executions
4. **Optimize Concurrency**: Monitor and adjust `max_concurrency` based on usage
5. **Add Choice State**: Consider adding explicit choice state for chunk count (currently handled by Map)
6. **Documentation**: Add inline comments explaining complex data flow paths

---

## Workflow Diagram

```
┌─────────────────┐
│ StartWorkflow   │ (Lambda - Entry Point)
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ InitializeJob   │ (Lambda)
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ SplitDocument   │ (Lambda)
└────────┬────────┘
         │
         ▼
┌─────────────────────────────────────┐
│ AnalyzeChunksParallel (Map)         │
│ MaxConcurrency: 10                  │
│                                     │
│  ┌───────────────────────────────┐ │
│  │ AnalyzeStructure (Lambda)     │ │
│  └──────────────┬────────────────┘ │
│                 ▼                   │
│  ┌───────────────────────────────┐ │
│  │ RetrieveAllKBQueries (Lambda) │ │
│  └──────────────┬────────────────┘ │
│                 ▼                   │
│  ┌───────────────────────────────┐ │
│  │ AnalyzeWithKB (Lambda)         │ │
│  └────────────────────────────────┘ │
└──────────────┬──────────────────────┘
               │
               ▼
┌──────────────────────────────┐
│ StoreChunkAnalyses (Lambda)  │
└──────────────┬───────────────┘
               │
               ▼
┌──────────────────────────────┐
│ MergeChunkResults (Lambda)   │
└──────────────┬───────────────┘
               │
               ▼
┌──────────────────────────────┐
│ GenerateRedline (Lambda)      │
└──────────────┬───────────────┘
               │
               ▼
┌──────────────────────────────┐
│ SaveResults (Lambda)          │
└──────────────┬───────────────┘
               │
               ▼
┌──────────────────────────────┐
│ CleanupSession (Lambda)       │
└───────────────────────────────┘

Error Path (from any state):
         │
         ▼
┌──────────────────────────────┐
│ HandleError (Lambda)          │
└───────────────────────────────┘
```

---

## Conclusion

The Step Functions workflow is well-designed with proper error handling, parallelization, and S3-based payload management. The unified workflow approach simplifies maintenance while handling both single and chunked documents efficiently. Key areas for improvement include enhanced error handling coverage and monitoring.

