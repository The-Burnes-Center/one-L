/**
 * Configuration utility for the One-L application
 * Loads configuration from config.json created during CDK deployment
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
    console.log('Development mode: using environment variables');
    console.log('REACT_APP_API_GATEWAY_URL:', process.env.REACT_APP_API_GATEWAY_URL);
    console.log('REACT_APP_USER_POOL_ID:', process.env.REACT_APP_USER_POOL_ID);
    console.log('REACT_APP_USER_POOL_CLIENT_ID:', process.env.REACT_APP_USER_POOL_CLIENT_ID);
    
    config = {
      apiGatewayUrl: process.env.REACT_APP_API_GATEWAY_URL || '',
      userPoolId: process.env.REACT_APP_USER_POOL_ID || '',
      userPoolClientId: process.env.REACT_APP_USER_POOL_CLIENT_ID || '',
      userPoolDomain: process.env.REACT_APP_USER_POOL_DOMAIN || '',
      region: process.env.REACT_APP_REGION || 'us-east-1',
      stackName: process.env.REACT_APP_STACK_NAME || 'OneLStack',
      knowledgeManagementUploadEndpointUrl: process.env.REACT_APP_KNOWLEDGE_UPLOAD_URL || '',
      knowledgeManagementRetrieveEndpointUrl: process.env.REACT_APP_KNOWLEDGE_RETRIEVE_URL || '',
      knowledgeManagementDeleteEndpointUrl: process.env.REACT_APP_KNOWLEDGE_DELETE_URL || ''
    };
    console.log('Development config:', config);
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
      console.error('Config file contains unresolved CDK tokens:', configData);
      throw new Error('Config file contains unresolved CDK tokens - deployment may be incomplete');
    }
    
    // Validate that essential config values are present and not empty
    if (!configData.apiGatewayUrl || !configData.userPoolId || !configData.userPoolClientId) {
      throw new Error('Config file is missing essential values');
    }
    
    console.log('Successfully loaded config from config.json:', configData);
    config = configData;
    
    return config;
  } catch (error) {
    console.error('Failed to load production config, falling back to environment variables:', error);
    
    // Fallback to environment variables even in production
    console.warn('Using environment variables for configuration due to config.json issues');
    
    // Log all environment variables for debugging
    console.log('Environment variables loaded:');
    console.log('REACT_APP_API_GATEWAY_URL:', process.env.REACT_APP_API_GATEWAY_URL);
    console.log('REACT_APP_USER_POOL_ID:', process.env.REACT_APP_USER_POOL_ID);
    console.log('REACT_APP_USER_POOL_CLIENT_ID:', process.env.REACT_APP_USER_POOL_CLIENT_ID);
    console.log('REACT_APP_USER_POOL_DOMAIN:', process.env.REACT_APP_USER_POOL_DOMAIN);
    console.log('REACT_APP_REGION:', process.env.REACT_APP_REGION);
    console.log('REACT_APP_STACK_NAME:', process.env.REACT_APP_STACK_NAME);
    
    config = {
      apiGatewayUrl: process.env.REACT_APP_API_GATEWAY_URL || '',
      userPoolId: process.env.REACT_APP_USER_POOL_ID || '',
      userPoolClientId: process.env.REACT_APP_USER_POOL_CLIENT_ID || '',
      userPoolDomain: process.env.REACT_APP_USER_POOL_DOMAIN || '',
      region: process.env.REACT_APP_REGION || 'us-east-1',
      stackName: process.env.REACT_APP_STACK_NAME || 'OneLStack',
      knowledgeManagementUploadEndpointUrl: process.env.REACT_APP_KNOWLEDGE_UPLOAD_URL || '',
      knowledgeManagementRetrieveEndpointUrl: process.env.REACT_APP_KNOWLEDGE_RETRIEVE_URL || '',
      knowledgeManagementDeleteEndpointUrl: process.env.REACT_APP_KNOWLEDGE_DELETE_URL || ''
    };
    
    console.log('Final config object:', config);
    
    // Check if fallback config is valid
    const missingEnvVars = [];
    if (!config.apiGatewayUrl) missingEnvVars.push('REACT_APP_API_GATEWAY_URL');
    if (!config.userPoolId) missingEnvVars.push('REACT_APP_USER_POOL_ID');
    if (!config.userPoolClientId) missingEnvVars.push('REACT_APP_USER_POOL_CLIENT_ID');
    
    if (missingEnvVars.length > 0) {
      console.error('Missing essential environment variables:', missingEnvVars);
      console.error('Application may not function correctly without these values');
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
  getAuthConfig,
  isConfigValid
}; 