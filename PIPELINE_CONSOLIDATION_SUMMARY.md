# Pipeline Consolidation Summary

## Overview
Consolidated the two separate pipelines (chunked and single document) into a unified pipeline that works for both cases. This eliminates code duplication, fixes bugs, and simplifies maintenance.

## Changes Made

### 1. Created Unified Lambda Functions

#### `analyze_structure` (replaces `analyze_chunk_structure` and `analyze_document_structure`)
- **Location**: `one_l/agent_api/functions/stepfunctions/analyze_structure/lambda_function.py`
- **Features**:
  - Handles both chunks (`chunk_s3_key`) and documents (`document_s3_key`)
  - Always includes chunk context when `chunk_num` and `total_chunks` are provided
  - For single documents: uses `chunk_num=0, total_chunks=1`
  - Automatically detects document format (PDF/DOCX)

#### `retrieve_all_kb_queries` (replaces parallel Map state with `retrieve_kb_query`)
- **Location**: `one_l/agent_api/functions/stepfunctions/retrieve_all_kb_queries/lambda_function.py`
- **Features**:
  - Retrieves all KB queries in a single lambda using `concurrent.futures.ThreadPoolExecutor`
  - Max concurrency: 20 workers (matches previous parallel map)
  - Stores all results in a single S3 file: `{session_id}/kb_results/{job_id}_all_queries.json`
  - Continues with successful queries if some fail (error handling)
  - Returns summary: `{results_s3_key, results_count, queries_count, success_count, failed_count}`

#### `analyze_with_kb` (replaces `analyze_chunk_with_kb` and `analyze_document_with_kb`)
- **Location**: `one_l/agent_api/functions/stepfunctions/analyze_with_kb/lambda_function.py`
- **Features**:
  - Handles both chunks and documents
  - Loads KB results from single S3 file (from `retrieve_all_kb_queries`)
  - Always includes chunk context when provided
  - For chunks: stores result in S3, returns reference
  - For single docs: returns conflicts directly (or S3 reference if large)

### 2. Updated Step Functions Definition

#### Unified Workflow
- **Location**: `one_l/agent_api/functions/stepfunctions/stepfunctions.py`
- **Changes**:
  - Removed parallel Map states for KB retrieval
  - Created unified workflow: `analyze_structure` → `retrieve_all_kb_queries` → `analyze_with_kb`
  - Fixed bucket_name bug: Changed `$.split_result.bucket_name` to `$.bucket_name` in chunk item_selector (line 413)
  - Both paths (chunked and single) use the same unified workflow
  - Single document path uses separate state instances with correct payload paths

#### Workflow Structure

**For Multiple Chunks (chunk_count > 1):**
```
analyze_chunks_map (Map, max_concurrency=10)
  └─ For each chunk:
      ├─ analyze_structure
      ├─ retrieve_all_kb_queries
      └─ analyze_with_kb (stores in S3, returns reference)
  └─ store_chunk_analyses
  └─ merge_chunk_results (loads from S3, outputs to $.conflicts_result)
```

**For Single Document (chunk_count = 1):**
```
analyze_structure_single
  └─ retrieve_all_kb_queries_single
  └─ analyze_with_kb_single (outputs directly to $.conflicts_result)
```

### 3. Deleted Old Lambda Functions

Removed duplicate lambda function directories:
- `analyze_chunk_structure/` ❌
- `analyze_document_structure/` ❌
- `analyze_chunk_with_kb/` ❌
- `analyze_document_with_kb/` ❌
- `retrieve_kb_query/` ❌ (replaced by `retrieve_all_kb_queries`)

### 4. Bug Fixes

#### Fixed bucket_name Path Bug
- **Problem**: Line 360 tried to access `$.split_result.bucket_name` inside chunk Map state, but at that level it's just `$.bucket_name`
- **Solution**: Updated chunk item_selector to use `$.bucket_name` directly (already extracted from `$.split_result.bucket_name`)

#### Fixed Missing Fixes in Parallel Pipeline
- All fixes now apply to both paths since they use the same unified functions

### 5. Updated Comments

- Updated `generate_redline/lambda_function.py` comment to reflect new unified functions

## Benefits

1. **Single Codebase**: One set of lambdas to maintain instead of two
2. **Bug Fixes Apply Everywhere**: Fix once, works for both paths
3. **Simpler KB Retrieval**: No parallel map state, no merging needed
4. **Better Error Handling**: Single point of failure for KB retrieval, continues with successful queries
5. **Reduced Lambda Invocations**: Fewer invocations = lower cost
6. **Easier Testing**: Test one set of functions instead of two
7. **Consistent Behavior**: Both paths use the same logic, ensuring consistency

## Testing Recommendations

1. **Single Document Path**: Test with `chunk_count = 1`
   - Verify `chunk_num=0, total_chunks=1` is passed correctly
   - Verify KB results are retrieved and stored correctly
   - Verify conflicts_result is set correctly

2. **Chunked Path**: Test with `chunk_count > 1`
   - Verify each chunk processes correctly
   - Verify chunk results are stored in S3
   - Verify merge_chunk_results loads from S3 correctly

3. **KB Retrieval**: Test with multiple queries
   - Verify all queries are retrieved in parallel (within single lambda)
   - Verify results are stored in single S3 file
   - Verify error handling (some queries fail, others succeed)

4. **Edge Cases**:
   - Empty queries array
   - Very large KB results
   - PDF vs DOCX documents
   - Single chunk (edge case between single doc and multiple chunks)

## Migration Notes

- **Backward Compatibility**: Old lambdas are deleted, so this is a clean cutover
- **No Data Migration Needed**: S3 structure remains the same
- **Step Functions**: State machine definition updated, will need redeployment

## Files Changed

### New Files
- `one_l/agent_api/functions/stepfunctions/analyze_structure/lambda_function.py`
- `one_l/agent_api/functions/stepfunctions/retrieve_all_kb_queries/lambda_function.py`
- `one_l/agent_api/functions/stepfunctions/analyze_with_kb/lambda_function.py`

### Modified Files
- `one_l/agent_api/functions/stepfunctions/stepfunctions.py` (major refactor)
- `one_l/agent_api/functions/stepfunctions/generate_redline/lambda_function.py` (comment update)

### Deleted Files
- `one_l/agent_api/functions/stepfunctions/analyze_chunk_structure/` (entire directory)
- `one_l/agent_api/functions/stepfunctions/analyze_document_structure/` (entire directory)
- `one_l/agent_api/functions/stepfunctions/analyze_chunk_with_kb/` (entire directory)
- `one_l/agent_api/functions/stepfunctions/analyze_document_with_kb/` (entire directory)
- `one_l/agent_api/functions/stepfunctions/retrieve_kb_query/` (entire directory)

