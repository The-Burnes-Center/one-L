# Security Incident Response - Exposed Private Key

## ⚠️ CRITICAL: Private Key Exposed in Repository

**Date:** November 10, 2025  
**Issue:** Google Cloud service account private key was committed to GitHub  
**Status:** 🔴 **ACTION REQUIRED**

## Immediate Actions Required

### 1. Revoke the Exposed Service Account Key

**Go to Google Cloud Console:**
1. Navigate to: https://console.cloud.google.com/iam-admin/serviceaccounts
2. Find service account: `one-l-document-ai@intense-subject-477818-h6.iam.gserviceaccount.com`
3. Click on the service account
4. Go to "Keys" tab
5. Find the key with ID: `1307d1a94cb80278bad150ac0bb4859fe5d79c94`
6. Click "Delete" to revoke it immediately

### 2. Create a New Service Account Key

1. In the same service account page, click "Add Key" → "Create new key"
2. Choose "JSON" format
3. Download the new key file
4. **DO NOT commit this file to git**

### 3. Encode the New Credentials

```bash
python scripts/encode_google_credentials.py path/to/new-credentials.json
```

### 4. Update Lambda with New Credentials

Deploy with the new encoded credentials:

```bash
cdk deploy --context googleCredentialsJson="<new-encoded-string>"
```

## What Was Exposed

- **Service Account:** `one-l-document-ai@intense-subject-477818-h6.iam.gserviceaccount.com`
- **Key ID:** `1307d1a94cb80278bad150ac0bb4859fe5d79c94`
- **Repository:** The-Burnes-Center/one-L
- **File:** `GOOGLE_CREDENTIALS_SETUP.md` (now removed)

## Prevention Measures

✅ **Removed:** `GOOGLE_CREDENTIALS_SETUP.md` from repository  
✅ **Updated:** Documentation to warn against committing credentials  
✅ **Added:** Helper script for encoding credentials locally  

## Best Practices Going Forward

1. **Never commit:**
   - Raw JSON credential files
   - Base64-encoded credentials
   - Private keys in any format
   - API keys or secrets

2. **Always use:**
   - CDK context for deployment-time secrets
   - Environment variables (set locally, not in code)
   - AWS Secrets Manager for production
   - `.gitignore` to exclude credential files

3. **Before committing:**
   - Review all files for sensitive data
   - Use `git diff` to check changes
   - Consider using pre-commit hooks to scan for secrets

## Verification

After revoking the old key and creating a new one:
1. Test that the old key no longer works
2. Deploy with new credentials
3. Verify Google Document AI still functions correctly

## Timeline

- **20:14:38 UTC Nov 10, 2025:** Credentials committed to repository
- **20:21 UTC Nov 10, 2025:** GitGuardian alert received
- **20:30 UTC Nov 10, 2025:** Credentials removed from repository
- **Pending:** Revoke old key and create new one

