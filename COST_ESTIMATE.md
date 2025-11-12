# Cost Estimate for Processing 40-Page Vendor Submission

## Google Document AI Costs

### Form Parser Pricing (as of 2025)
- **$1.50 per 1,000 pages** for Form Parser
- **$0.0015 per page**

### Your 40-Page Document
- **40 pages × $0.0015 = $0.06** (6 cents)

## AWS Costs (Additional)

### 1. Lambda Execution
- **Memory:** 2048 MB
- **Duration:** ~2-5 minutes for 40 pages
- **Cost:** ~$0.00001667 per GB-second
- **Estimate:** ~$0.01 - $0.03 per document

### 2. Amazon Bedrock (Claude Sonnet 4 for AI Analysis)
- **Input:** ~$3.00 per 1M input tokens
- **Output:** ~$15.00 per 1M output tokens
- **40 pages ≈ 20,000-30,000 tokens**
- **Estimate:** ~$0.10 - $0.30 per document

### 3. S3 Storage
- **Storage:** $0.023 per GB/month
- **Requests:** $0.005 per 1,000 PUT requests
- **Estimate:** ~$0.001 per document (negligible)

### 4. DynamoDB
- **Storage:** $0.25 per GB/month
- **Read/Write:** $1.25 per million requests
- **Estimate:** ~$0.001 per document (negligible)

### 5. API Gateway
- **First 1M requests:** Free
- **After:** $3.50 per million
- **Estimate:** Free for most use cases

## Total Cost Per 40-Page Document

| Service | Cost |
|---------|------|
| Google Document AI | $0.06 |
| AWS Lambda | $0.01 - $0.03 |
| Amazon Bedrock (AI Analysis) | $0.10 - $0.30 |
| S3 + DynamoDB | $0.002 |
| **TOTAL** | **$0.17 - $0.39 per document** |

## Monthly Cost Estimates

### Light Usage (10 documents/month)
- **Total:** ~$1.70 - $3.90/month

### Medium Usage (50 documents/month)
- **Total:** ~$8.50 - $19.50/month

### Heavy Usage (200 documents/month)
- **Total:** ~$34 - $78/month

## Cost Optimization Tips

1. **Use PyMuPDF Fallback:** If Google Document AI fails, PyMuPDF is free (no API costs)
2. **Free Tier:** Google Cloud offers $300 free credit (91 days remaining for you)
3. **AWS Free Tier:** Lambda, S3, DynamoDB have generous free tiers
4. **Batch Processing:** Process multiple documents in one Lambda invocation to save on cold starts

## Notes

- **Google Cloud Free Credit:** You have $300 credit with 91 days remaining
  - This covers ~5,000 documents (40 pages each) for FREE
  - After free credit: ~$0.06 per document for Google Document AI

- **Most Expensive Component:** Amazon Bedrock (Claude AI analysis) - $0.10-$0.30 per document
- **Cheapest Component:** Google Document AI - $0.06 per document

## Comparison: Google Document AI vs PyMuPDF

| Feature | Google Document AI | PyMuPDF (Free) |
|---------|-------------------|----------------|
| **Cost** | $0.06 per 40 pages | FREE |
| **Quality** | Excellent (better formatting) | Good (may lose some formatting) |
| **Speed** | ~10-30 seconds | ~5-15 seconds |
| **Reliability** | High (cloud service) | High (local processing) |

**Recommendation:** Use Google Document AI for better quality, but PyMuPDF fallback ensures you always have a free option.

