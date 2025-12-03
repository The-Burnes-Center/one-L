"""
Merge chunk results Lambda function.
Merges conflicts from all chunks, renumbers Additional-[#] conflicts, deduplicates.
Handles chunk results stored in S3 to avoid Step Functions payload size limits.
"""

import json
import boto3
import logging
import os
from agent_api.agent.prompts.models import ConflictDetectionOutput, ConflictModel

logger = logging.getLogger()
logger.setLevel(logging.INFO)

s3_client = boto3.client('s3')

# Import progress tracker
try:
    from shared.progress_tracker import update_progress
except ImportError:
    update_progress = None

def lambda_handler(event, context):
    """
    Merge conflicts from all chunks into single result.
    
    Args:
        event: Lambda event with chunk_results (list of chunk result dicts with S3 references)
        context: Lambda context
        
    Returns:
        Dict with conflicts_s3_key, conflicts_count, has_results (always stores in S3)
    """
    try:
        chunk_results = event.get('chunk_results', [])
        bucket_name = event.get('bucket_name') or os.environ.get('AGENT_PROCESSING_BUCKET')
        
        if not chunk_results:
            # Return empty result
            output = ConflictDetectionOutput(
                explanation="No chunks to merge",
                conflicts=[]
            )
            return output.model_dump()
        
        # Parse all chunk results
        all_conflicts = []
        explanations = []
        global_additional_counter = 0
        
        for chunk_idx, chunk_result_data in enumerate(chunk_results):
            try:
                # CRITICAL: Chunk results are always stored in S3
                # Load from S3 using results_s3_key
                if isinstance(chunk_result_data, dict) and chunk_result_data.get('results_s3_key'):
                    # This chunk's result is stored in S3 (always the case now)
                    results_s3_key = chunk_result_data.get('results_s3_key')
                    chunk_num = chunk_result_data.get('chunk_num', chunk_idx)
                    
                    try:
                        s3_response = s3_client.get_object(Bucket=bucket_name, Key=results_s3_key)
                        chunk_result_json = s3_response['Body'].read().decode('utf-8')
                        chunk_result = ConflictDetectionOutput.model_validate_json(chunk_result_json)
                        logger.info(f"Loaded chunk {chunk_num} result from S3: {results_s3_key}")
                    except Exception as e:
                        logger.error(f"CRITICAL: Failed to load chunk {chunk_num} result from S3 {results_s3_key}: {e}")
                        raise  # Fail fast - chunk results must be in S3
                elif isinstance(chunk_result_data, dict) and 'conflicts' in chunk_result_data:
                    # Fallback: inline dict (shouldn't happen, but handle for backward compatibility)
                    logger.warning(f"Chunk {chunk_idx} result is inline (should be in S3) - loading inline")
                    chunk_result = ConflictDetectionOutput.model_validate(chunk_result_data)
                elif isinstance(chunk_result_data, str):
                    # Fallback: inline JSON string (shouldn't happen, but handle for backward compatibility)
                    logger.warning(f"Chunk {chunk_idx} result is inline JSON (should be in S3) - loading inline")
                    chunk_result = ConflictDetectionOutput.model_validate_json(chunk_result_data)
                else:
                    logger.error(f"Invalid chunk result format for chunk {chunk_idx}: {type(chunk_result_data)}")
                    continue
                
                # Collect explanation
                if chunk_result.explanation:
                    explanations.append(f"Chunk {chunk_idx + 1}: {chunk_result.explanation}")
                
                # Process conflicts and renumber Additional-[#] conflicts
                for conflict in chunk_result.conflicts:
                    # Renumber Additional-[#] conflicts to ensure sequential numbering
                    if conflict.clarification_id.startswith('Additional-'):
                        global_additional_counter += 1
                        # Create new conflict with renumbered ID
                        conflict_dict = conflict.model_dump()
                        conflict_dict['clarification_id'] = f'Additional-{global_additional_counter}'
                        all_conflicts.append(ConflictModel(**conflict_dict))
                        logger.debug(f"Renumbered Additional conflict to Additional-{global_additional_counter}")
                    else:
                        # Keep vendor-provided IDs as-is
                        all_conflicts.append(conflict)
                
                logger.info(f"Merged {len(chunk_result.conflicts)} conflicts from chunk {chunk_idx + 1}")
                
            except Exception as e:
                logger.warning(f"Error processing chunk {chunk_idx + 1}: {e}")
                continue
        
        # Deduplicate conflicts based on clarification_id and vendor_quote
        seen_conflicts = set()
        deduplicated_conflicts = []
        
        for conflict in all_conflicts:
            # Create unique key from clarification_id and first 100 chars of vendor_quote
            conflict_key = (
                conflict.clarification_id,
                conflict.vendor_quote[:100] if conflict.vendor_quote else ""
            )
            
            if conflict_key not in seen_conflicts:
                seen_conflicts.add(conflict_key)
                deduplicated_conflicts.append(conflict)
            else:
                logger.debug(f"Deduplicated conflict: {conflict.clarification_id}")
        
        # Combine explanations
        combined_explanation = " ".join(explanations) if explanations else "Analysis completed across multiple document chunks."
        
        # Create validated output
        output = ConflictDetectionOutput(
            explanation=combined_explanation,
            conflicts=deduplicated_conflicts
        )
        
        logger.info(f"Merged {len(deduplicated_conflicts)} total conflicts from {len(chunk_results)} chunks")
        
        # Update progress
        job_id = event.get('job_id')
        timestamp = event.get('timestamp')
        session_id = event.get('session_id', 'unknown')
        if update_progress and job_id and timestamp:
            update_progress(
                job_id, timestamp, 'merging_results',
                f'Merged analysis results from {len(chunk_results)} chunks, found {len(deduplicated_conflicts)} conflicts...'
            )
        
        # CRITICAL: Always store result in S3 and return only S3 reference
        # Step Functions has 256KB limit - merged conflicts can be large
        result_dict = output.model_dump()
        result_json = json.dumps(result_dict)
        result_size = len(result_json.encode('utf-8'))
        
        try:
            s3_key_result = f"{session_id}/merged_results/{job_id}_merged_conflicts.json"
            s3_client.put_object(
                Bucket=bucket_name,
                Key=s3_key_result,
                Body=result_json.encode('utf-8'),
                ContentType='application/json'
            )
            logger.info(f"Stored merged conflicts result ({result_size} bytes) in S3: {s3_key_result}")
            
            # Return only S3 reference (never return data directly)
            return {
                'conflicts_s3_key': s3_key_result,
                'conflicts_count': len(deduplicated_conflicts),
                'has_results': True
            }
        except Exception as s3_error:
            logger.error(f"CRITICAL: Failed to store merged conflicts result in S3: {s3_error}")
            raise  # Fail fast if S3 storage fails
        
    except Exception as e:
        logger.error(f"Error in merge_chunk_results: {e}")
        raise

