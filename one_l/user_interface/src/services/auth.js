/**
 * Simple Authentication service for Cognito Hosted UI
 * Leverages Cognito to handle all authentication complexity
 */

import { getAuthConfig } from '../utils/config';

class AuthService {
  constructor() {
    this.cognitoDomain = null;
    this.clientId = null;
    this.redirectUri = window.location.origin;
    this.initialized = false;
  }

  /**
   * Initialize authentication service
   */
  async initialize() {
    if (this.initialized) {
      return true;
    }

    try {
      const config = await getAuthConfig();
      // userPoolDomain already contains the full URL with https://
      //       this.cognitoDomain = `https://${config.userPoolDomain}.auth.${config.region}.amazoncognito.com`;
      this.cognitoDomain = config.userPoolDomain;
      this.clientId = config.userPoolClientId;
      
      // Check if we just returned from Cognito
      await this.handleCallback();
      
      this.initialized = true;
      return true;
    } catch (error) {

      return false;
    }
  }

  /**
   * Redirect to Cognito hosted UI for login
   */
  login() {
    const state = Math.random().toString(36).substring(2, 15);
    sessionStorage.setItem('auth_state', state);
    
    const loginUrl = `${this.cognitoDomain}/login?` +
      `client_id=${this.clientId}&` +
      `response_type=code&` +
      `scope=email+openid+profile&` +
      `redirect_uri=${encodeURIComponent(this.redirectUri)}&` +
      `state=${state}`;
    

    window.location.href = loginUrl;
  }

  /**
   * Handle callback from Cognito
   */
  async handleCallback() {
    const urlParams = new URLSearchParams(window.location.search);
    const code = urlParams.get('code');
    const state = urlParams.get('state');
    const error = urlParams.get('error');

    if (error) {

      return;
    }

    if (code) {
      // Validate state to prevent CSRF
      const storedState = sessionStorage.getItem('auth_state');
      if (state !== storedState) {

        return;
      }

      try {
        // Exchange code for tokens
        await this.exchangeCodeForTokens(code);
        
        // Clean up URL
        window.history.replaceState({}, document.title, window.location.pathname);
        sessionStorage.removeItem('auth_state');
      } catch (error) {

      }
    }
  }

  /**
   * Exchange code for tokens (the only token operation needed)
   */
  async exchangeCodeForTokens(code) {
    console.log('Exchanging code for tokens:', {
      cognitoDomain: this.cognitoDomain,
      clientId: this.clientId,
      redirectUri: this.redirectUri,
      code: code.substring(0, 10) + '...'
    });

    const response = await fetch(`${this.cognitoDomain}/oauth2/token`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/x-www-form-urlencoded',
      },
      body: new URLSearchParams({
        grant_type: 'authorization_code',
        client_id: this.clientId,
        code: code,
        redirect_uri: this.redirectUri,
      }),
    });

    if (!response.ok) {
      const errorText = await response.text();
      console.error('Token exchange failed:', response.status, errorText);
      throw new Error(`Token exchange failed: ${response.status} - ${errorText}`);
    }

    const tokens = await response.json();
    
    // Store minimal info - just enough to know user is logged in
    sessionStorage.setItem('access_token', tokens.access_token);
    sessionStorage.setItem('id_token', tokens.id_token);
    
    // Decode user info from ID token (no validation needed for display purposes)
    const payload = JSON.parse(atob(tokens.id_token.split('.')[1]));
    sessionStorage.setItem('user_email', payload.email);
    sessionStorage.setItem('user_name', payload.name || payload.email);
    sessionStorage.setItem('user_id', payload.sub);
    
    console.log('Successfully exchanged code for tokens');
  }

  /**
   * Logout user
   */
  logout() {
    // Clear local storage
    sessionStorage.clear();
    
    // Redirect to Cognito logout
    const logoutUrl = `${this.cognitoDomain}/logout?` +
      `client_id=${this.clientId}&` +
      `logout_uri=${encodeURIComponent(this.redirectUri)}`;
    
    console.log('Redirecting to Cognito logout:', logoutUrl);
    window.location.href = logoutUrl;
  }

  /**
   * Check if user is authenticated
   */
  isUserAuthenticated() {
    return !!sessionStorage.getItem('access_token');
  }

  /**
   * Get current user info
   */
  getCurrentUser() {
    if (!this.isUserAuthenticated()) return null;
    
    return {
      email: sessionStorage.getItem('user_email'),
      name: sessionStorage.getItem('user_name'),
      sub: sessionStorage.getItem('user_id')
    };
  }

  /**
   * Get user ID for session management
   */
  getUserId() {
    return sessionStorage.getItem('user_id');
  }

  /**
   * Get authorization header for API calls
   */
  getAuthorizationHeader() {
    const token = sessionStorage.getItem('access_token');
    return token ? { 'Authorization': `Bearer ${token}` } : {};
  }
}

// Export singleton instance
const authService = new AuthService();
export default authService;