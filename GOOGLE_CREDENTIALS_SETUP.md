# Google Cloud Credentials Setup for Lambda

## Quick Setup

Your Google Cloud service account credentials have been encoded. Here's how to add them to Lambda:

### Option 1: Via CDK Context (Recommended for Deployment)

Add the encoded credentials when deploying:

```bash
cdk deploy --context googleCredentialsJson="<paste-the-encoded-string-here>"
```

**Your encoded credentials:**
```
eyJ0eXBlIjogInNlcnZpY2VfYWNjb3VudCIsICJwcm9qZWN0X2lkIjogImludGVuc2Utc3ViamVjdC00Nzc4MTgtaDYiLCAicHJpdmF0ZV9rZXlfaWQiOiAiMTMwN2QxYTk0Y2I4MDI3OGJhZDE1MGFjMGJiNDg1OWZlNWQ3OWM5NCIsICJwcml2YXRlX2tleSI6ICItLS0tLUJFR0lOIFBSSVZBVEUgS0VZLS0tLS1cbk1JSUV2UUlCQURBTkJna3Foa2lHOXcwQkFRRUZBQVNDQktjd2dnU2pBZ0VBQW9JQkFRQ25RUjNlMWpkVVkyNnFcblY5SU9HZkE1bHZtdmw4MmRybjRLZFBvZnc1bFM4OWlsa096T24vMGE4akpXWXZHV3VqVEtzQnBPMHBrcEUyaVFcbkVidDN5OGxONXg1L1JSK3J3cWJOMHFyTnN5bmVTNWZQZ3hGdzZFNGlKWUlLRllUcFpDYzNPYjNmVVNSaHc3dGtcblJyZjJoSkdDQ0dJdWNkbnJoZFNWcThybnN4bnFzT1dRUG02RTFPbmxBd2ZLazZuKy9BVnAzdFFHQ3lnMWt5QW5cbkgyR3JmMURvZmZxMUpyU0hySlVBc3pRSDFrQVpjSTJQQ0tHdHFIYjRNZlRnQTZtS01qejZoYURmL1VNUUVhNlZcbmlLL096UUsrQlIyNUg2WDhYZS9tOGJJZmpUSmlpZXY1ZkE5bnYyNlVHbGt0T0M1N0toS3pjcjlpVndHWExjWWFcbkFxQnNKOXduQWdNQkFBRUNnZ0VBQlBtcFowOE0vMHdqNXhwTXE5RzJ0U2ZhRHBDZjg3QlVuSUFGVHJ1VlRJUG5cbmJtbGt1ZkxGWnk5TU13aW5jaG5nNXlTYzY3cFZCd1ZHUjM3WGlrVThIRDZqU0Jyb3ljVzFDNHRZUEYwM2doN1NcbmljQTIzUnB6OUkwVUZMTzJLL0VvUDNyT3ZKMWZ6UzZ5M05WRHRLZ1Y0ZjNZMmRQV0xTemllWDlPUGwwMENLaWxcbkhkLzdBRHZLS0QxQzY2bEVWL200eGlIaFJNWG83SmJsM04vVWp4a2JuaVRYbzVSNFRhelBsR3ptdDk0VXdNWFVcbnhSd0tJNmIrL1RWUys0VGtheHNlNUZoc0dSUHdLRDJGb0dNc2VEdXQ4VHhPdWhRczFpQmdIZVNkOEZnR3F4QWJcbjI5ZmU5NnQ1Z2l0UFpZK0ZkemVhRWthNHlLWk5yT2I4ZGJxcndOb2VsUUtCZ1FEY082TytqY2NYK1VobW5ieFdcbjdzNUtNeGQ2VVB0ZjRpajg1b083Y3cyWlhsQTg5d2VZamthQ014TDNHd04vZEhnc2MxcXI4MjhVbkdsZnEwUUZcbnZpSWlnTkIvUUgramwzOWlvYktDMzE0MnZZOE1IUUhGQVdQQTJyc2llS2JNbDE3bGRlc0FRZHVDL3crbzlVNDNcbjI5SFJNYUd5THY3VEU0VEdSSE16dE0xNGt3S0JnUURDYXRsNCt6ZktLMXkzeFlsalVJUEdxRmk2M0Y0RlVuUCtcbkhyUUVZaDF6RjFrclpzaENYWEpBbWZTbnR4Tm80UWsvYnZWdHVZekFTY0NNS25UdDdOSXdObTdBNkxIS1FhaHdcbjNUWDlOdGxXS0RlUlEzM1E2NVhrYk5TdjlmVG9EQkU3Y01JaFpVSDhDeDVQdCtSUU5zNytPVXExSzVYYmZ1bGNcbnRqbHFTS3V1blFLQmdRQ0FybVJWMGhlVHZYZDlaZ3NITEkvaXNRbTEwWjJmZjlEOVBGK2Fablo2dDYzZ2dXS3hcbjArZ0U2WHphWDdGaWhwWTFPczJ2RFJWSmtMN05SSkFCWHdBbzh1VmdoVHBQUnhVS2QzcUxsNkpBRC9DR1htaUxcblNPMlZZUGpaQW5CTHVPS2M1cEtDV3ZpOUNQV0lmcFRPZEtXYk93bkV5RXJpNEZQRFdYbUtxOGttK3dLQmdBOFBcbm9mdEtVdDhaanR3NXRGUDZSOHNhL1l5MFI2Qlg2OTV5MkhWQ2VJK1M3bmg3Uk9aSFFQT2FPYWJJZXZ0eiszaHJcbml4M044d1p1Y0RrcmpOVmx0RDdCNk1DUEJqN3A2VGVkRzNLYlRpanJncXFCTlB6N1V5aFgrZjRMcXNaVE1QNk9cbjFLc3JvZm41am9hVWMxNTNjSCtuUm85VWFnNlAvVm9PVDlKWkFOdk5Bb0dBVFNvcFExL2V6djg4dWNrSUQ4V0Ncbm95S3hIN0IzdE1tNW8zNEhtRURoQytSVlNjSm5pZ083eHVPOVlUakVsL0Y4RFJKYUxsS1BGNG1kK0pFb2xSWmdcbjlHNCt2c1pBcmNqM1RVU0JPV1ZqQ1F1WnlnVkZ5dXVxdmpOc1JXT2FrQ1I5S25VTXVqaFZZK3FNRGhnNjhUamdcbkNBYUQvUG9ySTFoby9GQ1RreXdXV2RBPVxuLS0tLS1FTkQgUFJJVkFURSBLRVktLS0tLVxuIiwgImNsaWVudF9lbWFpbCI6ICJvbmUtbC1kb2N1bWVudC1haUBpbnRlbnNlLXN1YmplY3QtNDc3ODE4LWg2LmlhbS5nc2VydmljZWFjY291bnQuY29tIiwgImNsaWVudF9pZCI6ICIxMDEyOTIzOTY1NTYyMjA0ODg2NTciLCAiYXV0aF91cmkiOiAiaHR0cHM6Ly9hY2NvdW50cy5nb29nbGUuY29tL28vb2F1dGgyL2F1dGgiLCAidG9rZW5fdXJpIjogImh0dHBzOi8vb2F1dGgyLmdvb2dsZWFwaXMuY29tL3Rva2VuIiwgImF1dGhfcHJvdmlkZXJfeDUwOV9jZXJ0X3VybCI6ICJodHRwczovL3d3dy5nb29nbGVhcGlzLmNvbS9vYXV0aDIvdjEvY2VydHMiLCAiY2xpZW50X3g1MDlfY2VydF91cmwiOiAiaHR0cHM6Ly93d3cuZ29vZ2xlYXBpcy5jb20vcm9ib3QvdjEvbWV0YWRhdGEveDUwOS9vbmUtbC1kb2N1bWVudC1haSU0MGludGVuc2Utc3ViamVjdC00Nzc4MTgtaDYuaWFtLmdzZXJ2aWNlYWNjb3VudC5jb20iLCAidW5pdmVyc2VfZG9tYWluIjogImdvb2dsZWFwaXMuY29tIn0=
```

### Option 2: Via Environment Variable (Before CDK Synth)

```bash
export GOOGLE_APPLICATION_CREDENTIALS_JSON="<paste-encoded-string-here>"
cdk deploy
```

### Option 3: Add to constants.py (Not Recommended - Security Risk)

You could add it to `constants.py`, but this is **NOT recommended** as it stores credentials in source code.

## What's Already Configured

✅ **Project ID:** `intense-subject-477818-h6`  
✅ **Processor ID:** `51f27557dca2483d`  
✅ **Location:** `us`  
✅ **Credentials:** Encoded and ready to add

## Next Steps

1. **Deploy with credentials:**
   ```bash
   cdk deploy --context googleCredentialsJson="<paste-encoded-string-above>"
   ```

2. **Or set environment variable:**
   ```bash
   export GOOGLE_APPLICATION_CREDENTIALS_JSON="<paste-encoded-string-above>"
   cdk deploy
   ```

3. **Test the deployment** - The system will automatically:
   - Try Google Document AI first
   - Fall back to PyMuPDF if Google fails
   - Log which method was used

## Security Notes

- The credentials are base64-encoded (not encrypted) - this is safe for Lambda environment variables
- Never commit the encoded string to git
- Consider using AWS Secrets Manager for production deployments (more secure)

## Troubleshooting

- **"Authentication failed"**: Check that the encoded credentials string is correct
- **"Processor not found"**: Verify processor ID in `constants.py`
- **"API not enabled"**: Make sure Document AI API is enabled in Google Cloud Console

