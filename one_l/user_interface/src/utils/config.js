/**
 * Configuration utility for the One-L application
 * Loads configuration from config.json generated post-deployment by Lambda function
 * The config.json contains real deployment values (API Gateway URLs, Cognito IDs, etc.)
 */

let config = null;

/**
 * Load configuration from config.json
 */
const loadConfig = async () => {
  if (config) {
    return config;
  }
  
  // Check if we're in development mode
  const isDevelopment = process.env.NODE_ENV === 'development';
  
  if (isDevelopment) {
    // Use environment variables directly in development

    
    config = {
      apiGatewayUrl: process.env.REACT_APP_API_GATEWAY_URL || '',
      userPoolId: process.env.REACT_APP_USER_POOL_ID || '',
      userPoolClientId: process.env.REACT_APP_USER_POOL_CLIENT_ID || '',
      userPoolDomain: process.env.REACT_APP_USER_POOL_DOMAIN || '',
      region: process.env.REACT_APP_REGION || 'us-east-1',
      stackName: process.env.REACT_APP_STACK_NAME || 'OneLStack',
      knowledgeManagementUploadEndpointUrl: process.env.REACT_APP_KNOWLEDGE_UPLOAD_URL || '',
      knowledgeManagementRetrieveEndpointUrl: process.env.REACT_APP_KNOWLEDGE_RETRIEVE_URL || '',
      knowledgeManagementDeleteEndpointUrl: process.env.REACT_APP_KNOWLEDGE_DELETE_URL || '',
      knowledgeManagementSyncEndpointUrl: process.env.REACT_APP_KNOWLEDGE_SYNC_URL || '',
      webSocketUrl: process.env.REACT_APP_WEBSOCKET_URL || ''
    };


    return config;
  }
  
  // Production: first try cached config, then config.json
  try {
    // Try to load from config.json
    const response = await fetch('/config.json');
    
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}: ${response.statusText}`);
    }
    
    const contentType = response.headers.get('content-type');
    if (!contentType || !contentType.includes('application/json')) {
      throw new Error('Invalid content type - expected JSON');
    }
    
    const configData = await response.json();
    
    // Check if config contains unresolved CDK tokens
    const configString = JSON.stringify(configData);
    if (configString.includes('${Token[') || configString.includes('${AWS.')) {

      throw new Error('Config file contains unresolved CDK tokens - deployment may be incomplete');
    }
    
    // Validate that essential config values are present and not empty
    if (!configData.apiGatewayUrl || !configData.userPoolId || !configData.userPoolClientId) {
      throw new Error('Config file is missing essential values');
    }
    


    config = configData;
    
    return config;
  } catch (error) {

    
    // Fallback to environment variables even in production

    
    // Log configuration loading status (without sensitive values)

    
    config = {
      apiGatewayUrl: process.env.REACT_APP_API_GATEWAY_URL || '',
      userPoolId: process.env.REACT_APP_USER_POOL_ID || '',
      userPoolClientId: process.env.REACT_APP_USER_POOL_CLIENT_ID || '',
      userPoolDomain: process.env.REACT_APP_USER_POOL_DOMAIN || '',
      region: process.env.REACT_APP_REGION || 'us-east-1',
      stackName: process.env.REACT_APP_STACK_NAME || 'OneLStack',
      knowledgeManagementUploadEndpointUrl: process.env.REACT_APP_KNOWLEDGE_UPLOAD_URL || '',
      knowledgeManagementRetrieveEndpointUrl: process.env.REACT_APP_KNOWLEDGE_RETRIEVE_URL || '',
      knowledgeManagementDeleteEndpointUrl: process.env.REACT_APP_KNOWLEDGE_DELETE_URL || '',
      knowledgeManagementSyncEndpointUrl: process.env.REACT_APP_KNOWLEDGE_SYNC_URL || '',
      webSocketUrl: process.env.REACT_APP_WEBSOCKET_URL || ''
    };
    

    
    // Check if fallback config is valid
    const missingEnvVars = [];
    if (!config.apiGatewayUrl) missingEnvVars.push('REACT_APP_API_GATEWAY_URL');
    if (!config.userPoolId) missingEnvVars.push('REACT_APP_USER_POOL_ID');
    if (!config.userPoolClientId) missingEnvVars.push('REACT_APP_USER_POOL_CLIENT_ID');
    
    if (missingEnvVars.length > 0) {


    } else {
      console.info('Successfully loaded configuration from environment variables');
    }
    
    return config;
  }
};

/**
 * Get configuration value
 */
const getConfig = async (key) => {
  const cfg = await loadConfig();
  return cfg[key];
};

/**
 * Get API Gateway URL
 */
const getApiGatewayUrl = async () => {
  return await getConfig('apiGatewayUrl');
};

/**
 * Get WebSocket URL
 */
const getWebSocketUrl = async () => {
  return await getConfig('webSocketUrl');
};

/**
 * Get authentication configuration
 */
const getAuthConfig = async () => {
  const cfg = await loadConfig();
  return {
    userPoolId: cfg.userPoolId,
    userPoolClientId: cfg.userPoolClientId,
    userPoolDomain: cfg.userPoolDomain,
    region: cfg.region
  };
};

/**
 * Check if configuration is loaded and valid
 */
const isConfigValid = async () => {
  const cfg = await loadConfig();
  return cfg && cfg.apiGatewayUrl && cfg.userPoolId && cfg.userPoolClientId;
};

export {
  loadConfig,
  getConfig,
  getApiGatewayUrl,
  getWebSocketUrl,
  getAuthConfig,
  isConfigValid
}; 