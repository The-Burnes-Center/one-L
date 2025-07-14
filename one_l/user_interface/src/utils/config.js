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
  
  try {
    const response = await fetch('/config.json');
    if (!response.ok) {
      throw new Error('Failed to load configuration');
    }
    config = await response.json();
    return config;
  } catch (error) {
    console.error('Error loading configuration:', error);
    
    // Fallback configuration for development
    config = {
      apiGatewayUrl: process.env.REACT_APP_API_GATEWAY_URL || '',
      userPoolId: process.env.REACT_APP_USER_POOL_ID || '',
      userPoolClientId: process.env.REACT_APP_USER_POOL_CLIENT_ID || '',
      userPoolDomain: process.env.REACT_APP_USER_POOL_DOMAIN || '',
      region: process.env.REACT_APP_REGION || 'us-east-1',
      stackName: process.env.REACT_APP_STACK_NAME || 'OneLStack'
    };
    
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