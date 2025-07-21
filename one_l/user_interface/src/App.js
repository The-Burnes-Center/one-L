/**
 * Main App Component for One-L Application
 */

import React, { useState, useEffect } from 'react';
import FileUpload from './components/FileUpload';
import { isConfigValid, loadConfig } from './utils/config';

const App = () => {
  const [configLoaded, setConfigLoaded] = useState(false);
  const [configError, setConfigError] = useState('');

  useEffect(() => {
    const initializeApp = async () => {
      try {
        await loadConfig();
        const isValid = await isConfigValid();
        
        if (!isValid) {
          setConfigError('Configuration is incomplete. Please check your deployment.');
          return;
        }
        
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
      </div>

      <FileUpload />
    </div>
  );
};

export default App; 