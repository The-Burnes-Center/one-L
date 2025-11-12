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
   * Decode a JWT payload safely (supports base64url encoding).
   */
  decodeTokenPayload(token) {
    if (!token || typeof token !== 'string') {
      return null;
    }

    try {
      const parts = token.split('.');
      if (parts.length < 2) {
        return null;
      }

      const payloadPart = parts[1]
        .replace(/-/g, '+')
        .replace(/_/g, '/')
        .padEnd(parts[1].length + (4 - (parts[1].length % 4 || 4)) % 4, '=');

      const decoded = atob(payloadPart);
      return JSON.parse(decoded);
    } catch (error) {
      console.warn('Failed to decode token payload', error);
      return null;
    }
  }

  /**
   * Extract user groups from a token payload.
   */
  extractGroupsFromPayload(payload) {
    if (!payload) {
      return [];
    }

    const groups = payload['cognito:groups'] || payload['groups'] || payload['group'] || [];
    if (Array.isArray(groups)) {
      return groups.filter(Boolean);
    }

    if (typeof groups === 'string' && groups.length > 0) {
      return [groups];
    }

    return [];
  }

  /**
   * Determine whether the user is an admin based on token claims.
   */
  determineIsAdminFromClaims(payload, groups = this.extractGroupsFromPayload(payload)) {
    if (!payload && groups.length === 0) {
      return false;
    }

    const hasAdminGroup = groups.some(group => {
      if (typeof group !== 'string') return false;
      const normalized = group.toLowerCase();
      return normalized.includes('admin');
    });

    const customAttribute = payload?.['custom:is_admin'];
    const declaredRole = payload?.role;

    const customIsAdmin = typeof customAttribute === 'string'
      ? customAttribute.toLowerCase() === 'true'
      : customAttribute === true;

    const roleIsAdmin = typeof declaredRole === 'string'
      ? declaredRole.toLowerCase() === 'admin'
      : declaredRole === 'admin';

    return hasAdminGroup || customIsAdmin || roleIsAdmin;
  }

  /**
   * Cache user-related attributes from a token payload into session storage.
   */
  cacheUserAttributes(payload) {
    if (!payload) {
      return;
    }

    if (payload.email) {
      sessionStorage.setItem('user_email', payload.email);
    }

    if (payload.name || payload.email) {
      sessionStorage.setItem('user_name', payload.name || payload.email);
    }

    if (payload.sub) {
      sessionStorage.setItem('user_id', payload.sub);
    }

    const groups = this.extractGroupsFromPayload(payload);

    if (groups.length > 0) {
      sessionStorage.setItem('user_groups', JSON.stringify(groups));
    } else {
      sessionStorage.removeItem('user_groups');
    }

    const isAdmin = this.determineIsAdminFromClaims(payload, groups);
    sessionStorage.setItem('is_admin', isAdmin ? 'true' : 'false');
  }

  /**
   * Populate cached user attributes when tokens already exist in storage.
   */
  populateCachedUserAttributes() {
    const idToken = sessionStorage.getItem('id_token');
    if (!idToken) {
      return;
    }

    const payload = this.decodeTokenPayload(idToken);
    if (payload) {
      this.cacheUserAttributes(payload);
    }
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
      this.cognitoDomain = config.userPoolDomain;
      this.clientId = config.userPoolClientId;
      
      // Check if we just returned from Cognito
      await this.handleCallback();

      // Ensure cached user attributes are available when tokens already exist
      if (this.isUserAuthenticated()) {
        this.populateCachedUserAttributes();
      }
      
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
    const payload = this.decodeTokenPayload(tokens.id_token);
    this.cacheUserAttributes(payload);
    
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
   * Get list of groups for the current user.
   */
  getUserGroups() {
    const storedGroups = sessionStorage.getItem('user_groups');
    if (!storedGroups) {
      return [];
    }

    try {
      const parsed = JSON.parse(storedGroups);
      return Array.isArray(parsed) ? parsed : [];
    } catch (error) {
      console.warn('Failed to parse stored user groups', error);
      return [];
    }
  }

  /**
   * Check whether the current user has admin privileges.
   */
  isUserAdmin() {
    const storedFlag = sessionStorage.getItem('is_admin');
    if (storedFlag === 'true') {
      return true;
    }

    if (storedFlag === 'false') {
      return false;
    }

    const groups = this.getUserGroups();
    return groups.some(group => {
      if (typeof group !== 'string') return false;
      return group.toLowerCase().includes('admin');
    });
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