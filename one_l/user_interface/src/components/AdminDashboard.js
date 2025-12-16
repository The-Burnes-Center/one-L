/**
 * Admin Dashboard Component for One-L Application
 * Provides administrative functionality including knowledge base data management
 */

import React from 'react';
import KnowledgeUpload from './KnowledgeUpload';

const AdminDashboard = () => {
  return (
    <div className="admin-dashboard">
      <div className="dashboard-header">
        <h1>Knowledge Base Management</h1>
        <p>Upload documents and manage knowledge base contents</p>
      </div>

      <div className="dashboard-content">
        <KnowledgeUpload />
      </div>
    </div>
  );
};

export default AdminDashboard; 