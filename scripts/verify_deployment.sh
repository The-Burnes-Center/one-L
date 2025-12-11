#!/bin/bash
# Verification script to check if deployment fixes are working

echo "=== Deployment Verification ==="
echo ""

# Check session-management Lambda environment variables
# Stack name must be provided via environment variable or read from constants.py
# Usage: STACK_NAME=YourStackName ./scripts/verify_deployment.sh
if [ -z "$STACK_NAME" ]; then
    # Try to read from constants.py if available
    if [ -f "constants.py" ]; then
        STACK_NAME=$(grep "^STACK_NAME" constants.py | cut -d'"' -f2 | head -1)
    fi
    if [ -z "$STACK_NAME" ]; then
        echo "Error: STACK_NAME environment variable not set and cannot read from constants.py"
        echo "Usage: STACK_NAME=YourStackName ./scripts/verify_deployment.sh"
        exit 1
    fi
fi
echo "1. Checking session-management Lambda environment variables..."
echo "   Using stack name: $STACK_NAME"
SESSION_MGMT_FUNCTION=$(aws lambda list-functions --query "Functions[?contains(FunctionName, \`$STACK_NAME\`) && contains(FunctionName, \`session-management\`)].FunctionName" --output text 2>/dev/null)

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
    
    EXPECTED_TABLE="${STACK_NAME}-analysis-results"
    if [ "$ANALYSIS_TABLE" = "$EXPECTED_TABLE" ]; then
        echo "   ✓ ANALYSIS_RESULTS_TABLE is correct: $ANALYSIS_TABLE"
    else
        echo "   ✗ ANALYSIS_RESULTS_TABLE is incorrect: $ANALYSIS_TABLE (expected: $EXPECTED_TABLE)"
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
echo "3. Checking Step Functions state machine..."
STATE_MACHINE_ARN=$(aws stepfunctions list-state-machines --query "stateMachines[?contains(name, \`$STACK_NAME\`) && contains(name, \`document-review\`)].stateMachineArn" --output text 2>/dev/null | head -1)

if [ -n "$STATE_MACHINE_ARN" ]; then
    echo "   Found Step Functions state machine: $STATE_MACHINE_ARN"
    STATE_MACHINE_NAME=$(aws stepfunctions describe-state-machine --state-machine-arn "$STATE_MACHINE_ARN" --query 'name' --output text 2>/dev/null)
    echo "   State Machine Name: $STATE_MACHINE_NAME"
    
    # Check recent executions
    echo ""
    echo "   Recent executions (last 5):"
    aws stepfunctions list-executions \
        --state-machine-arn "$STATE_MACHINE_ARN" \
        --max-items 5 \
        --query 'executions[*].[name,status,startDate]' \
        --output table 2>/dev/null || echo "   Could not retrieve executions"
    
    echo "   ✓ Step Functions state machine is deployed and accessible"
else
    echo "   ✗ Step Functions state machine not found"
fi

echo ""
echo "=== Verification Complete ==="

