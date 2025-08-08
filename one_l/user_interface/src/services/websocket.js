/**
 * WebSocket Service for Real-time Communication
 * Handles WebSocket connections for live document processing updates
 */

import authService from './auth';
import { getWebSocketUrl } from '../utils/config';

class WebSocketService {
  constructor() {
    this.ws = null;
    this.connectionId = null;
    this.isConnected = false;
    this.reconnectAttempts = 0;
    this.maxReconnectAttempts = 5;
    this.reconnectDelay = 1000; // Start with 1 second
    this.maxReconnectDelay = 30000; // Max 30 seconds
    this.messageHandlers = new Map();
    this.subscriptions = new Set();
    
    // Bind methods to preserve context
    this.onOpen = this.onOpen.bind(this);
    this.onMessage = this.onMessage.bind(this);
    this.onError = this.onError.bind(this);
    this.onClose = this.onClose.bind(this);
  }

  /**
   * Connect to WebSocket server
   */
  async connect() {
    if (this.isConnected || (this.ws && this.ws.readyState === WebSocket.CONNECTING)) {
      console.log('WebSocket already connected or connecting');
      return Promise.resolve();
    }

    try {
      const wsUrl = await getWebSocketUrl();
      console.log('Retrieved WebSocket URL:', wsUrl);
      const userId = authService.getUserId();
      
      if (!wsUrl) {
        console.error('WebSocket URL is null/undefined from config');
        throw new Error('WebSocket URL not configured');
      }
      
      if (!userId) {
        throw new Error('User not authenticated');
      }

      // Add authentication parameters to WebSocket URL
      const url = new URL(wsUrl);
      url.searchParams.append('userId', userId);
      
      console.log('Connecting to WebSocket:', url.toString());
      
      return new Promise((resolve, reject) => {
        this.ws = new WebSocket(url.toString());
        
        // Store resolve/reject for connection handling
        this._connectResolve = resolve;
        this._connectReject = reject;
        
        this.ws.onopen = this.onOpen;
        this.ws.onmessage = this.onMessage;
        this.ws.onerror = this.onError;
        this.ws.onclose = this.onClose;
        
        // Set connection timeout
        this._connectTimeout = setTimeout(() => {
          if (this.ws.readyState === WebSocket.CONNECTING) {
            this.ws.close();
            this._connectReject(new Error('WebSocket connection timeout'));
          }
        }, 10000); // 10 second timeout
      });
      
    } catch (error) {
      console.error('Failed to connect to WebSocket:', error);
      throw error;
    }
  }

  /**
   * Disconnect from WebSocket server
   */
  disconnect() {
    if (this.ws) {
      console.log('Disconnecting WebSocket');
      this.isConnected = false;
      this.ws.close(1000, 'Client disconnect');
      this.ws = null;
      this.connectionId = null;
      this.subscriptions.clear();
    }
  }

  /**
   * Subscribe to job updates
   */
  subscribeToJob(jobId, sessionId = null) {
    if (!this.isConnected) {
      console.warn('WebSocket not connected, queuing subscription');
      this.subscriptions.add({ type: 'job', jobId, sessionId });
      return;
    }

    const message = {
      action: 'subscribe',
      jobId: jobId,
      sessionId: sessionId
    };
    
    this.send(message);
    console.log('Subscribed to job updates:', jobId);
  }

  /**
   * Subscribe to all updates for a session
   */
  subscribeToSession(sessionId) {
    if (!this.isConnected) {
      console.warn('WebSocket not connected, queuing session subscription');
      this.subscriptions.add({ type: 'session', sessionId });
      return;
    }

    const message = {
      action: 'subscribe',
      sessionId: sessionId,
      subscribeToSession: true  // Flag to indicate session-level subscription
    };
    
    this.send(message);
    console.log('Subscribed to session updates:', sessionId);
  }

  /**
   * Unsubscribe from job updates
   */
  unsubscribeFromJob() {
    if (!this.isConnected) {
      return;
    }

    const message = {
      action: 'unsubscribe'
    };
    
    this.send(message);
    console.log('Unsubscribed from job updates');
  }

  /**
   * Send a message to the WebSocket server
   */
  send(message) {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(message));
    } else {
      console.warn('WebSocket not connected, cannot send message:', message);
    }
  }

  /**
   * Add a message handler for specific message types
   */
  onMessageType(type, handler) {
    if (!this.messageHandlers.has(type)) {
      this.messageHandlers.set(type, []);
    }
    this.messageHandlers.get(type).push(handler);
  }

  /**
   * Remove a message handler
   */
  offMessageType(type, handler) {
    if (this.messageHandlers.has(type)) {
      const handlers = this.messageHandlers.get(type);
      const index = handlers.indexOf(handler);
      if (index > -1) {
        handlers.splice(index, 1);
      }
    }
  }

  /**
   * Handle WebSocket connection open
   */
  onOpen(event) {
    console.log('WebSocket connected successfully');
    this.isConnected = true;
    this.reconnectAttempts = 0;
    this.reconnectDelay = 1000;
    
    if (this._connectTimeout) {
      clearTimeout(this._connectTimeout);
      this._connectTimeout = null;
    }
    
    if (this._connectResolve) {
      this._connectResolve();
      this._connectResolve = null;
      this._connectReject = null;
    }
    
    // Process queued subscriptions
    this.subscriptions.forEach(subscription => {
      if (subscription.type === 'job') {
        this.subscribeToJob(subscription.jobId, subscription.sessionId);
      } else if (subscription.type === 'session') {
        this.subscribeToSession(subscription.sessionId);
      }
    });
    this.subscriptions.clear();
    
    // Send periodic ping to keep connection alive
    this.startPingInterval();
  }

  /**
   * Handle WebSocket messages
   */
  onMessage(event) {
    try {
      const message = JSON.parse(event.data);
      console.log('WebSocket message received:', message);
      
      const messageType = message.type;
      
      // Handle connection establishment
      if (messageType === 'connection_established') {
        this.connectionId = message.connectionId;
        console.log('WebSocket connection ID:', this.connectionId);
        return;
      }
      
      // Call registered handlers for this message type
      if (this.messageHandlers.has(messageType)) {
        this.messageHandlers.get(messageType).forEach(handler => {
          try {
            handler(message);
          } catch (error) {
            console.error('Error in message handler:', error);
          }
        });
      }
      
      // Call generic message handlers
      if (this.messageHandlers.has('*')) {
        this.messageHandlers.get('*').forEach(handler => {
          try {
            handler(message);
          } catch (error) {
            console.error('Error in generic message handler:', error);
          }
        });
      }
      
    } catch (error) {
      console.error('Failed to parse WebSocket message:', error);
    }
  }

  /**
   * Handle WebSocket errors
   */
  onError(event) {
    console.error('WebSocket error:', event);
    
    if (this._connectReject) {
      this._connectReject(new Error('WebSocket connection failed'));
      this._connectReject = null;
      this._connectResolve = null;
    }
  }

  /**
   * Handle WebSocket connection close
   */
  onClose(event) {
    console.log('WebSocket connection closed:', event.code, event.reason);
    this.isConnected = false;
    this.connectionId = null;
    
    this.stopPingInterval();
    
    if (this._connectReject && !event.wasClean) {
      this._connectReject(new Error(`WebSocket connection closed: ${event.reason}`));
      this._connectReject = null;
      this._connectResolve = null;
    }
    
    // Attempt to reconnect if not a clean close
    if (!event.wasClean && this.reconnectAttempts < this.maxReconnectAttempts) {
      this.scheduleReconnect();
    }
  }

  /**
   * Schedule a reconnection attempt
   */
  scheduleReconnect() {
    this.reconnectAttempts++;
    const delay = Math.min(this.reconnectDelay * Math.pow(2, this.reconnectAttempts - 1), this.maxReconnectDelay);
    
    console.log(`Scheduling WebSocket reconnect attempt ${this.reconnectAttempts} in ${delay}ms`);
    
    setTimeout(() => {
      if (!this.isConnected) {
        console.log(`Attempting WebSocket reconnect (${this.reconnectAttempts}/${this.maxReconnectAttempts})`);
        this.connect().catch(error => {
          console.error('WebSocket reconnect failed:', error);
        });
      }
    }, delay);
  }

  /**
   * Start sending periodic pings to keep connection alive
   */
  startPingInterval() {
    this.stopPingInterval(); // Clear any existing interval
    
    this.pingInterval = setInterval(() => {
      if (this.isConnected) {
        this.send({ action: 'ping' });
      }
    }, 30000); // Ping every 30 seconds
  }

  /**
   * Stop the ping interval
   */
  stopPingInterval() {
    if (this.pingInterval) {
      clearInterval(this.pingInterval);
      this.pingInterval = null;
    }
  }

  /**
   * Get connection status
   */
  getConnectionStatus() {
    return {
      isConnected: this.isConnected,
      connectionId: this.connectionId,
      readyState: this.ws ? this.ws.readyState : WebSocket.CLOSED,
      reconnectAttempts: this.reconnectAttempts
    };
  }
}

// Export singleton instance
const webSocketService = new WebSocketService();
export default webSocketService;