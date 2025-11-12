# Google Document AI Setup Guide

## Step-by-Step Instructions

### Step 1: Enable Document AI API

1. In the Google Cloud Console, click the **search bar** at the top (or press `/`)
2. Type: `Document AI API`
3. Click on "Document AI API" from the results
4. Click the **"Enable"** button
5. Wait for it to enable (may take 1-2 minutes)

### Step 2: Create a Document AI Processor

1. In the search bar, type: `Document AI`
2. Click on "Document AI" (the main service, not the API)
3. In the left sidebar, click **"Processors"**
4. Click the **"+ Create Processor"** button
5. Choose processor type:
   - **Recommended:** Select **"Form Parser"** (best for general documents)
   - Alternative: "Document OCR" (for scanned documents)
6. Fill in the form:
   - **Processor name:** `one-l-pdf-converter` (or any name you prefer)
   - **Region:** Select **"us"** (or your preferred region)
   - **Click "Create"**
7. **IMPORTANT:** After creation, you'll see the processor details page
   - **Copy the Processor ID** (it looks like: `abc123def456` or similar)
   - This is your `GOOGLE_DOCUMENT_AI_PROCESSOR_ID`

### Step 3: Create Service Account for Authentication

1. In the search bar, type: `IAM & Admin` or `Service Accounts`
2. Click on "Service Accounts"
3. Click **"+ Create Service Account"**
4. Fill in:
   - **Service account name:** `one-l-document-ai`
   - **Service account ID:** (auto-filled, keep as is)
   - Click **"Create and Continue"**
5. Grant permissions:
   - **Role:** Select **"Document AI API User"** or **"Document AI API Client"**
   - Click **"Continue"**
6. Click **"Done"**
7. **Create and download key:**
   - Click on the service account you just created
   - Go to **"Keys"** tab
   - Click **"Add Key"** → **"Create new key"**
   - Choose **"JSON"** format
   - Click **"Create"** - this will download a JSON file
   - **SAVE THIS FILE SECURELY** - you'll need it for Lambda

### Step 4: Configure Your Project

1. Open `constants.py` in your project
2. Set the values:

```python
GOOGLE_CLOUD_PROJECT_ID = "intense-subject-477818-h6"  # Your project ID from the URL
GOOGLE_DOCUMENT_AI_PROCESSOR_ID = "YOUR_PROCESSOR_ID_HERE"  # From Step 2
GOOGLE_DOCUMENT_AI_LOCATION = "us"  # The region you selected in Step 2
```

### Step 5: Configure Lambda with Service Account Key

**IMPORTANT: Never commit credentials to git!**

You'll need to add the service account JSON key to your Lambda function. Use one of these options:

**Option A: Via CDK Context (Recommended)**
1. Encode your JSON credentials file using the helper script:
   ```bash
   python scripts/encode_google_credentials.py path/to/your-credentials.json
   ```
2. Copy the base64-encoded string
3. Deploy with CDK context (credentials are NOT stored in code):
   ```bash
   cdk deploy --context googleCredentialsJson="<paste-encoded-string-here>"
   ```

**Option B: Via Environment Variable**
1. Encode your JSON credentials file:
   ```bash
   python scripts/encode_google_credentials.py path/to/your-credentials.json
   ```
2. Set environment variable before deploying:
   ```bash
   export GOOGLE_APPLICATION_CREDENTIALS_JSON="<paste-encoded-string-here>"
   cdk deploy
   ```

**Option C: AWS Secrets Manager (Most Secure for Production)**
1. Store credentials in AWS Secrets Manager
2. Update Lambda code to read from Secrets Manager

### Step 6: Deploy

Once configured, deploy your changes:
```bash
cdk deploy
```

## Quick Reference

- **Project ID:** `intense-subject-477818-h6` (from your URL)
- **Processor ID:** Get from Step 2 (after creating processor)
- **Location:** `us` (or whatever region you choose)
- **Service Account:** Create in Step 3, download JSON key

## Troubleshooting

- **"API not enabled"**: Make sure Document AI API is enabled (Step 1)
- **"Processor not found"**: Check that processor ID is correct
- **"Authentication failed"**: Verify service account JSON key is correct
- **"Permission denied"**: Ensure service account has "Document AI API User" role

