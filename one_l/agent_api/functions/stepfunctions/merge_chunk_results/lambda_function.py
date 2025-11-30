"""
Merge chunk results Lambda function.
Merges conflicts from all chunks, renumbers Additional-[#] conflicts, deduplicates.
"""

import json
import boto3
import logging
from agent_api.agent.prompts.models import ConflictDetectionOutput, ConflictModel

logger = logging.getLogger()
logger.setLevel(logging.INFO)

def lambda_handler(event, context):
    """
    Merge conflicts from all chunks into single result.
    
    Args:
        event: Lambda event with chunk_results (list of ConflictDetectionOutput JSON strings)
        context: Lambda context
        
    Returns:
        ConflictDetectionOutput with merged conflicts
    """
    try:
        chunk_results = event.get('chunk_results', [])
        
        if not chunk_results:
            # Return empty result
            output = ConflictDetectionOutput(
                explanation="No chunks to merge",
                conflicts=[]
            )
            return {
                "statusCode": 200,
                "body": output.model_dump_json()
            }
        
        # Parse all chunk results
        all_conflicts = []
        explanations = []
        global_additional_counter = 0
        
        for chunk_idx, chunk_result_json in enumerate(chunk_results):
            try:
                if isinstance(chunk_result_json, str):
                    chunk_result = ConflictDetectionOutput.model_validate_json(chunk_result_json)
                else:
                    chunk_result = ConflictDetectionOutput.model_validate(chunk_result_json)
                
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
        
        return {
            "statusCode": 200,
            "body": output.model_dump_json()
        }
        
    except Exception as e:
        logger.error(f"Error in merge_chunk_results: {e}")
        raise

