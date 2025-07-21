/**
 * Main App Component for One-L Application
 */

import React, { useState, useEffect } from 'react';
import FileUpload from './components/FileUpload';
import { isConfigValid, loadConfig } from './utils/config';

const App = () => {
  const [configLoaded, setConfigLoaded] = useState(false);
  const [configError, setConfigError] = useState('');
  const [config, setConfig] = useState(null);

  useEffect(() => {
    const initializeApp = async () => {
      try {
        const cfg = await loadConfig();
        const isValid = await isConfigValid();
        
        if (!isValid) {
          setConfigError('Configuration is incomplete. Please check your deployment.');
          return;
        }
        
        setConfig(cfg);
        setConfigLoaded(true);
      } catch (error) {
        console.error('Failed to initialize app:', error);
        setConfigError('Failed to load application configuration.');
      }
    };

    initializeApp();
  }, []);

  if (configError) {
    return (
      <div className="container">
        <div className="card">
          <h1>One-L Document Management</h1>
          <div className="alert alert-error">
            <strong>Configuration Error:</strong> {configError}
          </div>
          <p>Please ensure the application is properly deployed and configured.</p>
        </div>
      </div>
    );
  }

  if (!configLoaded) {
    return (
      <div className="container">
        <div className="card">
          <h1>One-L Document Management</h1>
          <p>Loading configuration...</p>
        </div>
      </div>
    );
  }

  return (
    <div className="container">
      <div className="card">
        <h1>One-L Document Management System</h1>
        <p>Welcome to the One-L document management system. Upload and manage your documents securely.</p>
        
        {config && (
          <div style={{ 
            background: '#f8f9fa', 
            padding: '12px', 
            borderRadius: '4px', 
            marginBottom: '20px',
            fontSize: '14px',
            color: '#666'
          }}>
            <strong>Environment:</strong> {config.stackName || 'Unknown'}
          </div>
        )}
      </div>

      <FileUpload />
      
      <div className="card">
        <h3>About</h3>
        <p>
          This document management system allows you to upload, store, and manage your documents securely 
          using AWS S3 storage with CloudFront delivery.
        </p>
        <ul>
          <li>Upload multiple files at once</li>
          <li>Secure cloud storage with encryption</li>
          <li>Global content delivery via CloudFront</li>
          <li>File validation and size limits</li>
        </ul>
      </div>
    </div>
  );
};

export default App; 