#!/bin/bash
# Verification script to check if deployment fixes are working

echo "=== Deployment Verification ==="
echo ""

# Check session-management Lambda environment variables
echo "1. Checking session-management Lambda environment variables..."
SESSION_MGMT_FUNCTION=$(aws lambda list-functions --query 'Functions[?contains(FunctionName, `OneL-v2`) && contains(FunctionName, `session-management`)].FunctionName' --output text 2>/dev/null)

if [ -n "$SESSION_MGMT_FUNCTION" ]; then
    echo "   Found: $SESSION_MGMT_FUNCTION"
    echo ""
    echo "   Environment Variables:"
    aws lambda get-function-configuration \
        --function-name "$SESSION_MGMT_FUNCTION" \
        --query 'Environment.Variables' \
        --output json | jq '.'
    
    ANALYSIS_TABLE=$(aws lambda get-function-configuration \
        --function-name "$SESSION_MGMT_FUNCTION" \
        --query 'Environment.Variables.ANALYSIS_RESULTS_TABLE' \
        --output text 2>/dev/null)
    
    if [ "$ANALYSIS_TABLE" = "OneL-v2-analysis-results" ]; then
        echo "   ✓ ANALYSIS_RESULTS_TABLE is correct: $ANALYSIS_TABLE"
    else
        echo "   ✗ ANALYSIS_RESULTS_TABLE is incorrect: $ANALYSIS_TABLE (expected: OneL-v2-analysis-results)"
    fi
else
    echo "   ✗ session-management Lambda function not found"
fi

echo ""
echo "2. Checking recent CloudWatch logs for session-management..."
if [ -n "$SESSION_MGMT_FUNCTION" ]; then
    LOG_GROUP="/aws/lambda/$SESSION_MGMT_FUNCTION"
    echo "   Checking log group: $LOG_GROUP"
    
    # Check for environment variable logs
    aws logs filter-log-events \
        --log-group-name "$LOG_GROUP" \
        --filter-pattern "SESSION_MANAGEMENT_ENV" \
        --max-items 5 \
        --query 'events[*].message' \
        --output text 2>/dev/null | head -3
    
    # Check for AccessDeniedException errors
    echo ""
    echo "   Checking for AccessDeniedException errors (should be none)..."
    ERRORS=$(aws logs filter-log-events \
        --log-group-name "$LOG_GROUP" \
        --filter-pattern "AccessDeniedException" \
        --start-time $(($(date +%s) - 3600))000 \
        --query 'events[*].message' \
        --output text 2>/dev/null)
    
    if [ -z "$ERRORS" ]; then
        echo "   ✓ No AccessDeniedException errors in last hour"
    else
        echo "   ✗ Found AccessDeniedException errors:"
        echo "$ERRORS" | head -3
    fi
fi

echo ""
echo "3. Checking document-review Lambda..."
DOC_REVIEW_FUNCTION=$(aws lambda list-functions --query 'Functions[?contains(FunctionName, `OneL-v2`) && contains(FunctionName, `document-review`)].FunctionName' --output text 2>/dev/null)

if [ -n "$DOC_REVIEW_FUNCTION" ]; then
    echo "   Found: $DOC_REVIEW_FUNCTION"
    JOB_TABLE=$(aws lambda get-function-configuration \
        --function-name "$DOC_REVIEW_FUNCTION" \
        --query 'Environment.Variables.ANALYSIS_RESULTS_TABLE' \
        --output text 2>/dev/null)
    
    if [ "$JOB_TABLE" = "OneL-v2-analysis-results" ]; then
        echo "   ✓ ANALYSIS_RESULTS_TABLE is correct: $JOB_TABLE"
    else
        echo "   ✗ ANALYSIS_RESULTS_TABLE is incorrect: $JOB_TABLE (expected: OneL-v2-analysis-results)"
    fi
else
    echo "   ✗ document-review Lambda function not found"
fi

echo ""
echo "=== Verification Complete ==="

