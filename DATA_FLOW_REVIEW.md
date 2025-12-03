# Step Functions Data Flow Review

## Complete Data Flow Analysis

### 1. Entry Point: `start_workflow` Lambda
**Input**: API Gateway request body
**Output**: Starts Step Functions execution, returns `job_id` immediately

**Passes to Step Functions**:
- `job_id`, `timestamp`, `session_id`, `user_id`
- `document_s3_key`, `bucket_type`
- `terms_profile`, `knowledge_base_id`, `region`

---

### 2. `initialize_job` Lambda
**Input**: Full context from `start_workflow`
**Output**: Merged at `$.Payload` (becomes root state)
**Returns**:
- All input fields preserved
- `bucket_name` (resolved from `bucket_type`)
- `status: "processing"`

**Data Flow**: ✅ Correct
- Uses `output_path="$.Payload"` to merge with state
- All context flows through

---

### 3. `split_document` Lambda
**Input**: Full context from `initialize_job` (preserved)
**Output**: Merged at `$.split_result`
**Returns**:
- `chunk_count`: int
- `chunks`: List[{chunk_num, start_char, end_char, s3_key}]
- `bucket_name`: string (for downstream processing)

**Data Flow**: ✅ Correct
- Uses `result_path="$.split_result"` to merge
- Original context preserved at root level
- `bucket_name` included for chunk processing

---

### 4. `check_chunk_count` Choice State
**Condition**: `$.split_result.chunk_count > 1`
- **True** → Chunked path
- **False** → Single document path

**Data Flow**: ✅ Correct

---

### 5. CHUNKED PATH (chunks > 1)

#### 5.1 `analyze_chunks_map` Map State
**Items**: `$.split_result.chunks`
**Item Selector** (creates input for each chunk):
- Chunk data: `chunk_s3_key`, `chunk_num`, `start_char`, `end_char`
- Context: `bucket_name`, `total_chunks`, `job_id`, `session_id`, `user_id`, `document_s3_key`, `terms_profile`, `knowledge_base_id`, `region`, `timestamp`

**Data Flow**: ✅ Correct
- ✅ Fixed: Uses `$.split_result.bucket_name` (correct)

#### 5.2 `analyze_structure` (inside Map)
**Input**: From item_selector (chunk data + context)
**Output**: `$.structure_result`
**Returns**: `StructureAnalysisOutput` with `queries` array

**Data Flow**: ✅ Correct
- Receives `chunk_s3_key` from item_selector
- Receives `bucket_name` from item_selector
- Outputs to `$.structure_result`

#### 5.3 `retrieve_all_kb_queries` (inside Map)
**Input**: 
- `queries`: `$.structure_result.queries` ✅
- `knowledge_base_id`: `$.knowledge_base_id` ✅
- `region`: `$.region` ✅
- `job_id`: `$.job_id` ✅
- `session_id`: `$.session_id` ✅
- `bucket_name`: `$.bucket_name` ✅

**Output**: `$.kb_retrieval_result`
**Returns**: `{results_s3_key, results_count, queries_count, success_count, failed_count}`

**Data Flow**: ✅ Correct

#### 5.4 `analyze_with_kb` (inside Map)
**Input**:
- `chunk_s3_key`: `$.chunk_s3_key` ✅
- `bucket_name`: `$.bucket_name` ✅
- `kb_results_s3_key`: `$.kb_retrieval_result.results_s3_key` ✅
- Context: `chunk_num`, `total_chunks`, `start_char`, `end_char`, etc. ✅

**Output**: `$.analysis_result`
**Returns** (for chunks): `{chunk_num, results_s3_key, conflicts_count, has_results}`

**Data Flow**: ✅ Correct
- Stores result in S3, returns reference
- Map collects all into `$.chunk_analyses`

#### 5.5 `store_chunk_analyses`
**Input**:
- `kb_results`: `$.chunk_analyses` ✅
- `job_id`, `session_id`, `bucket_name` ✅

**Output**: `$.chunk_storage`
**Returns**: `{s3_key}`

**Data Flow**: ✅ Correct
- Stores aggregated chunk analyses if too large

#### 5.6 `merge_chunk_results`
**Input**:
- `chunk_results`: `$.chunk_analyses` ✅ (array of S3 references)
- `chunk_analyses_s3_key`: `$.chunk_storage.s3_key` ✅ (backup)
- `bucket_name`: `$.split_result.bucket_name` ✅
- `job_id`, `timestamp` ✅

**Output**: `$.conflicts_result`
**Returns**: `ConflictDetectionOutput` (merged conflicts)

**Data Flow**: ✅ Correct
- Loads each chunk result from S3 using `results_s3_key`
- Merges and outputs to `$.conflicts_result`

---

### 6. SINGLE DOCUMENT PATH (chunks = 1)

#### 6.1 `analyze_structure_single`
**Input**:
- `document_s3_key`: `$.document_s3_key` ✅
- `bucket_name`: `$.split_result.bucket_name` ✅
- `knowledge_base_id`, `region` ✅
- `chunk_num`: 0, `total_chunks`: 1 ✅

**Output**: `$.structure_result`
**Returns**: `StructureAnalysisOutput` with `queries` array

**Data Flow**: ✅ Correct

#### 6.2 `retrieve_all_kb_queries_single`
**Input**:
- `queries`: `$.structure_result.queries` ✅
- `knowledge_base_id`, `region`, `job_id`, `session_id` ✅
- `bucket_name`: `$.split_result.bucket_name` ✅

**Output**: `$.kb_retrieval_result`
**Returns**: `{results_s3_key, ...}`

**Data Flow**: ✅ Correct

#### 6.3 `analyze_with_kb_single`
**Input**:
- `document_s3_key`: `$.document_s3_key` ✅
- `bucket_name`: `$.split_result.bucket_name` ✅
- `kb_results_s3_key`: `$.kb_retrieval_result.results_s3_key` ✅
- `chunk_num`: 0, `total_chunks`: 1 ✅

**Output**: `$.conflicts_result` ✅ (directly to conflicts_result)
**Returns**: `ConflictDetectionOutput` (inline or S3 reference if large)

**Data Flow**: ✅ Correct
- Outputs directly to `$.conflicts_result` (not `$.analysis_result`)

---

### 7. COMMON FINAL STEPS

#### 7.1 `generate_redline`
**Input**:
- `conflicts_result`: `$.conflicts_result` ✅
- `document_s3_key`: `$.document_s3_key` ✅
- `bucket_type`: `$.bucket_type` ✅
- `session_id`, `user_id`, `job_id`, `timestamp` ✅

**Output**: `$.redline_result`
**Returns**: `{success, redlined_document_s3_key, error}`

**Data Flow**: ✅ Correct

#### 7.2 `save_results`
**Input**:
- `analysis_json`: `$.conflicts_result` ✅
- `document_s3_key`: `$.document_s3_key` ✅
- `redlined_s3_key`: `$.redline_result.redlined_document_s3_key` ✅
- `session_id`, `user_id`, `job_id`, `timestamp` ✅
- ❌ **MISSING**: `bucket_type` (lambda expects it but not passed)

**Output**: `$.save_result`
**Returns**: `{success, analysis_id, error}`

**Data Flow**: ⚠️ **ISSUE FOUND**
- Lambda expects `bucket_type` (defaults to 'agent_processing')
- Not passed in payload - will use default

#### 7.3 `cleanup_session`
**Input**: Full context (no explicit payload)
**Output**: `$.cleanup_result`
**Returns**: `{success, message}`

**Data Flow**: ✅ Correct
- Uses `session_id` and `user_id` from context

---

## Issues Found

### Issue 1: Missing `bucket_type` in `save_results` payload
**Location**: `stepfunctions.py` line 584-592
**Problem**: `save_results` lambda expects `bucket_type` but it's not passed
**Impact**: Will default to 'agent_processing' (may be incorrect)
**Fix**: Add `bucket_type` to payload

### Issue 2: `cleanup_session` has no explicit payload
**Location**: `stepfunctions.py` line 602-608
**Problem**: No payload specified, relies on context
**Impact**: Should work but not explicit
**Recommendation**: Add explicit payload for clarity

---

## Data Flow Summary

### Context Preservation
✅ All context (`job_id`, `session_id`, `user_id`, `document_s3_key`, `bucket_type`, etc.) flows through entire workflow

### Path-Specific Data
✅ Chunked path: Uses `$.split_result.bucket_name` correctly
✅ Single doc path: Uses `$.split_result.bucket_name` correctly
✅ Both paths converge to `$.conflicts_result` correctly

### S3 Storage Pattern
✅ Chunks: Always stored in S3, references in Map results
✅ Single docs: Stored if large, otherwise inline
✅ KB results: Always stored in single S3 file
✅ Merge step: Loads from S3 correctly

### Final Steps
✅ `generate_redline`: Receives `conflicts_result` correctly
⚠️ `save_results`: Missing `bucket_type` (uses default)
✅ `cleanup_session`: Works but not explicit

