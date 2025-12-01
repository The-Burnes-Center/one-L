# Pipeline Consolidation Analysis

## Current State: Two Separate Pipelines

### Pipeline 1: Chunked Path (chunks > 1)
```
analyze_chunks_map (Map state, max_concurrency=10)
  └─ For each chunk:
      ├─ analyze_chunk_structure
      ├─ retrieve_kb_queries_map (Map state, max_concurrency=20)
      │   └─ For each query: retrieve_kb_query
      └─ analyze_chunk_with_kb
  └─ store_chunk_analyses
  └─ merge_chunk_results
```

**Input Flow:**
- `analyze_chunks_map` receives chunks from `$.split_result.chunks`
- Each chunk gets: `chunk_s3_key`, `chunk_num`, `start_char`, `end_char`, `bucket_name`, etc.
- `analyze_chunk_structure` outputs: `$.structure_result` with `queries` array
- `retrieve_kb_queries_map` processes `$.structure_result.queries` in parallel
- **BUG**: Line 360 tries to access `$.split_result.bucket_name` but at chunk level it's `$.bucket_name`

**Output Flow:**
- Each chunk produces: `$.analysis_result` (stored in S3)
- All chunk results collected in `$.chunk_analyses`
- `merge_chunk_results` merges all conflicts

### Pipeline 2: Single Document Path (chunks = 1)
```
analyze_document_structure
  └─ retrieve_doc_kb_queries_map (Map state, max_concurrency=20)
      └─ For each query: retrieve_kb_query
  └─ store_doc_kb_results
  └─ analyze_document_with_kb
```

**Input Flow:**
- `analyze_document_structure` receives: `document_s3_key`, `bucket_name` (from `$.bucket_name`)
- Outputs: `$.structure_result` with `queries` array
- `retrieve_doc_kb_queries_map` processes queries in parallel
- Line 501 correctly uses `$.bucket_name`

**Output Flow:**
- KB results stored in S3 per-query
- Aggregated KB results stored in `$.kb_storage.s3_key`
- `analyze_document_with_kb` produces: `$.conflicts_result`

## Problems Identified

### 1. Code Duplication
- `analyze_chunk_structure` vs `analyze_document_structure` - **99% identical**
- `analyze_chunk_with_kb` vs `analyze_document_with_kb` - **95% identical**
- Only difference: chunk context string and chunk_num/total_chunks

### 2. Bug in Chunked Pipeline
**Location**: Line 360 in `stepfunctions.py`
```python
"bucket_name.$": "$.split_result.bucket_name"  # WRONG - doesn't exist at chunk level
```
**Should be**: `"bucket_name.$": "$.bucket_name"` (already extracted in item_selector)

### 3. Parallel KB Retrieval Overhead
- Current: Map state with 20 parallel lambdas, each storing results in S3
- Each lambda: S3 write, error handling, retry logic
- Better: Single lambda that retrieves all queries sequentially/with boto3 concurrency
- Benefits: Simpler code, fewer Lambda invocations, easier error handling

### 4. Maintenance Burden
- Fixes applied to single-document path don't automatically apply to chunked path
- Two codebases to maintain, test, and debug

## Proposed Solution: Unified Pipeline

### Single Unified Workflow
```
analyze_structure (unified - handles both chunk and document)
  └─ retrieve_all_kb_queries (single lambda - retrieves all queries)
  └─ analyze_with_kb (unified - handles both chunk and document)
```

### For Multiple Chunks:
```
analyze_chunks_map (Map state, max_concurrency=10)
  └─ For each chunk:
      ├─ analyze_structure (unified)
      ├─ retrieve_all_kb_queries (single lambda)
      └─ analyze_with_kb (unified)
  └─ store_chunk_analyses
  └─ merge_chunk_results
```

### For Single Document:
```
analyze_structure (unified)
  └─ retrieve_all_kb_queries (single lambda)
  └─ analyze_with_kb (unified)
```

## Implementation Plan

### Step 1: Create Unified Lambda Functions
1. **`analyze_structure`** - Replace both `analyze_chunk_structure` and `analyze_document_structure`
   - Accepts: `chunk_s3_key` OR `document_s3_key`
   - Detects which one is present
   - Adds chunk context if `chunk_num` and `total_chunks` present
   - Returns: `StructureAnalysisOutput`

2. **`retrieve_all_kb_queries`** - Replace parallel Map state
   - Accepts: `queries` array, `knowledge_base_id`, `region`, `job_id`, `session_id`, `bucket_name`
   - Loops through all queries, retrieves each one
   - Stores all results in single S3 file: `{session_id}/kb_results/{job_id}_all_queries.json`
   - Returns: `{results_s3_key, results_count, queries_count}`

3. **`analyze_with_kb`** - Replace both `analyze_chunk_with_kb` and `analyze_document_with_kb`
   - Accepts: `chunk_s3_key` OR `document_s3_key`, `kb_results_s3_key`, optional chunk context
   - Detects which one is present
   - Loads KB results from S3
   - Returns: `ConflictDetectionOutput` (or S3 reference if large)

### Step 2: Update Step Functions Definition
- Remove duplicate states
- Use unified lambdas
- Fix bucket_name path bug
- Simplify workflow

### Step 3: Remove Old Lambda Functions
- Delete `analyze_chunk_structure`
- Delete `analyze_document_structure`
- Delete `analyze_chunk_with_kb`
- Delete `analyze_document_with_kb`
- Keep `retrieve_kb_query` for now (or delete if fully replaced)

## Benefits

1. **Single Codebase**: One set of lambdas to maintain
2. **Bug Fixes Apply Everywhere**: Fix once, works for both paths
3. **Simpler KB Retrieval**: No parallel map state, no merging needed
4. **Better Error Handling**: Single point of failure for KB retrieval
5. **Reduced Lambda Invocations**: Fewer invocations = lower cost
6. **Easier Testing**: Test one set of functions instead of two

## Questions to Consider

1. **KB Retrieval Performance**: 
   - Current: 20 parallel lambdas (fast but complex)
   - Proposed: Single lambda with sequential retrieval (simpler but potentially slower)
   - **Alternative**: Single lambda with `concurrent.futures` for parallel retrieval within Lambda
   - **Recommendation**: Use concurrent.futures for best of both worlds

2. **Backward Compatibility**:
   - Do we need to keep old lambdas temporarily?
   - Can we do a clean cutover?

3. **Chunk Context**:
   - Should we always pass chunk context even for single documents?
   - Or detect based on presence of `chunk_num`?

