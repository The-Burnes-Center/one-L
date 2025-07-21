# One-L User Interface

This directory contains the React frontend for the One-L document management system.

## Architecture

The user interface is built using React and follows a clean, modular architecture:

```
src/
├── components/     # React components
│   └── FileUpload.js
├── services/       # API service layer
│   └── api.js
├── utils/          # Utility functions
│   └── config.js
├── App.js          # Main App component
└── index.js        # Entry point
```

## Key Features

- **Multiple File Upload**: Upload multiple files at once with drag-and-drop support
- **File Validation**: Client-side validation for file types and sizes
- **API Integration**: Uses API Gateway instead of direct AWS SDK calls
- **Configuration Management**: Dynamic configuration loading from CDK deployment
- **Error Handling**: Comprehensive error handling and user feedback
- **Responsive Design**: Modern, responsive UI with clean styling

## Development

### Prerequisites

- Node.js 16 or later
- npm or yarn

### Setup

1. Install dependencies:
   ```bash
   npm install
   ```

2. For local development, create a `.env` file with:
   ```
   REACT_APP_API_GATEWAY_URL=your-api-gateway-url
   REACT_APP_USER_POOL_ID=your-user-pool-id
   REACT_APP_USER_POOL_CLIENT_ID=your-user-pool-client-id
   REACT_APP_USER_POOL_DOMAIN=your-user-pool-domain
   REACT_APP_REGION=us-east-1
   REACT_APP_STACK_NAME=OneLStack
   ```

3. Start development server:
   ```bash
   npm start
   ```

### Building

To build the application for production:

```bash
npm run build
```

Or use the provided build script:

```bash
./build.sh
```

## Deployment

The application is deployed using AWS CDK:

1. **S3 Bucket**: Static website hosting
2. **CloudFront**: Global content delivery
3. **Automatic Configuration**: Configuration is automatically generated during CDK deployment

## Configuration

The application automatically loads configuration from `/config.json` which is generated during CDK deployment. This includes:

- API Gateway URL
- Cognito User Pool settings
- AWS region
- Stack name

## API Integration

The frontend communicates with the backend through the API Gateway:

- **Upload**: `POST /knowledge_management/upload`
- **Retrieve**: `POST /knowledge_management/retrieve`
- **Delete**: `DELETE /knowledge_management/delete`

## Security

- No AWS credentials stored in frontend
- All API calls go through API Gateway
- File validation on client and server side
- CORS properly configured

## File Structure Changes

**Previous structure (removed):**
```
user-interface/
└── app/
    ├── package.json (heavy dependencies)
    ├── src/
    │   ├── App.js
    │   ├── fileUpload.js (AWS SDK direct calls)
    │   └── index.js (redundant code)
    └── public/
```

**New structure:**
```
user_interface/
├── src/
│   ├── components/
│   ├── services/
│   ├── utils/
│   ├── App.js
│   └── index.js
├── public/
├── build/
├── package.json (clean dependencies)
├── build.sh
└── README.md
```

## Benefits of New Structure

1. **Clean Dependencies**: Only necessary packages included
2. **Better Organization**: Logical separation of concerns
3. **API Abstraction**: Centralized API service layer
4. **Configuration Management**: Dynamic configuration loading
5. **Error Handling**: Comprehensive error handling
6. **Multiple Files**: Support for multiple file uploads
7. **Security**: No AWS credentials in frontend code
8. **Maintainability**: Clean, documented code structure 