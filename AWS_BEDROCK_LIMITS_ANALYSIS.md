# AWS Bedrock Limits Analysis

## Model Configuration

**Model ID:** `us.anthropic.claude-sonnet-4-20250514-v1:0`

**Model Type:** Inference Profile (not foundation model directly)
- The model ID format `us.anthropic.claude-sonnet-4-20250514-v1:0` indicates this is an inference profile
- Inference profiles are AWS-managed endpoints that provide additional features and may have different rate limits than direct foundation model access

**Location in Code:**
- File: `one_l/agent_api/agent/model.py`
- Line: 35
- Configuration: `CLAUDE_MODEL_ID = "us.anthropic.claude-sonnet-4-20250514-v1:0"`

## AWS Bedrock Service Quotas

Based on AWS documentation and search results, AWS Bedrock enforces the following types of limits:

### 1. **Tokens Per Minute (TPM)**
- Controls the total number of tokens processed per minute
- Varies by:
  - Model (Claude Sonnet 4)
  - AWS Region
  - AWS Account (can be increased via Service Quotas)
  - Whether using inference profile vs foundation model

### 2. **Requests Per Minute (RPM)**
- Limits the number of API calls per minute
- For Claude 3 Sonnet (reference):
  - **us-east-1** and **us-west-2**: Up to 1,000 requests/minute
  - **Other regions**: 200 requests/minute
- Claude Sonnet 4 may have similar or different limits

### 3. **Region-Specific Limits**
- Different regions have different default quotas
- Primary regions (us-east-1, us-west-2) typically have higher limits
- Secondary regions have lower limits

## Current Implementation

### Retry Configuration
Located in `one_l/agent_api/agent/model.py`:

```python
MAX_RETRIES = 5                    # Total of 6 attempts (0-5)
BASE_DELAY = 1.0                   # Initial delay: 1 second
MAX_DELAY = 6.0                    # Maximum delay: 6 seconds
BACKOFF_MULTIPLIER = 2.0           # Exponential backoff multiplier
CALL_SPACING_DELAY = 1.0           # Minimum delay between calls
```

### Throttling Detection
The code detects throttling by checking for:
- `ThrottlingException` in error message
- `Too many tokens` in error message
- Keywords: `rate`, `throttl`, `limit` (case-insensitive)

### Retry Behavior
- **Attempt 1**: 1.0 second delay
- **Attempt 2**: 2.0 seconds delay (matches your log)
- **Attempt 3**: 4.0 seconds delay
- **Attempt 4**: 6.0 seconds delay (capped at MAX_DELAY)
- **Attempt 5**: 6.0 seconds delay
- **Attempt 6**: 6.0 seconds delay
- **After 6 attempts**: Raises exception

## Recommendations

### 1. **Check Your Actual Quotas**
Access AWS Service Quotas console:
- Navigate to: https://console.aws.amazon.com/servicequotas/home
- Select "Amazon Bedrock"
- Look for quotas related to:
  - `us.anthropic.claude-sonnet-4-20250514-v1:0` (inference profile)
  - `anthropic.claude-sonnet-4-20250514-v1:0` (foundation model)
  - Filter by your AWS region

### 2. **Identify Your Region**
The code accepts a region parameter but the `bedrock_client` is initialized without explicit region:
- File: `one_l/agent_api/agent/model.py`, line 32
- Current: `bedrock_client = boto3.client('bedrock-runtime', config=bedrock_config)`
- This uses the default AWS region from environment/Lambda context

**To verify your region:**
- Check Lambda environment variable `REGION`
- Check CloudWatch logs for region information
- Check AWS console for your deployment region

### 3. **Monitor Usage**
- Use CloudWatch metrics to track:
  - Request rate (RPM)
  - Token consumption (TPM)
  - Throttling frequency
- Set up alarms for approaching limits

### 4. **Request Quota Increase (if needed)**
If you're consistently hitting limits:
1. Go to AWS Service Quotas console
2. Find the specific quota for Claude Sonnet 4 in your region
3. Request an increase with justification:
   - Use case description
   - Expected TPM/RPM requirements
   - Usage patterns
   - Growth projections

### 5. **Optimize Request Patterns**
Current optimizations already in place:
- ✅ Exponential backoff retry logic
- ✅ Call spacing delay (1 second minimum between calls)
- ✅ Graceful queuing

Additional considerations:
- Consider batching multiple operations if possible
- Cache results when appropriate
- Review if all API calls are necessary

## Important Notes

1. **Inference Profile vs Foundation Model**
   - Your code uses an inference profile (`us.anthropic.claude-sonnet-4-20250514-v1:0`)
   - Inference profiles may have different quotas than direct foundation model access
   - Check quotas for both if available

2. **Model Availability**
   - Claude Sonnet 4 (20250514) is a relatively new model
   - Ensure it's available in your deployment region
   - Some regions may have limited availability or different quotas

3. **Token Counting**
   - Your configuration uses:
     - `MAX_TOKENS = 8000` (output tokens)
     - `THINKING_BUDGET_TOKENS = 4000` (thinking tokens)
   - Both input and output tokens count toward TPM limits
   - Large documents consume more tokens

## Next Steps

1. **Immediate**: Check AWS Service Quotas console for your specific limits
2. **Short-term**: Monitor CloudWatch metrics to understand usage patterns
3. **Long-term**: Request quota increase if needed, or optimize request patterns

## References

- AWS Bedrock Service Quotas: https://docs.aws.amazon.com/bedrock/latest/userguide/quotas.html
- AWS Service Quotas Console: https://console.aws.amazon.com/servicequotas/home
- AWS Bedrock Supported Models: https://docs.aws.amazon.com/bedrock/latest/userguide/inference-supported.html


