/**
 * Admin Dashboard Component for One-L Application
 * Provides administrative functionality including knowledge base data management
 */

import React from 'react';
import KnowledgeUpload from './KnowledgeUpload';

const AdminDashboard = ({ activeTab, onTabChange }) => {
  // Remove local state since it's now managed by App component

  const sections = [
    {
      id: 'data',
      label: 'Data',
      component: KnowledgeUpload
    }
  ];

  const renderActiveSection = () => {
    const activeTabConfig = sections.find(section => section.id === activeTab);
    if (activeTabConfig) {
      const Component = activeTabConfig.component;
      return <Component />;
    }
    return null;
  };

  return (
    <div className="admin-dashboard">
      <div className="dashboard-header">
        <h1>Admin Dashboard</h1>
        <p>Manage knowledge base data and system configuration</p>
      </div>

      <div className="dashboard-tabs">
        {sections.map(section => (
          <button
            key={section.id}
            className={`dashboard-tab ${activeTab === section.id ? 'active' : ''}`}
            onClick={() => onTabChange(section.id)}
          >
            {section.label}
          </button>
        ))}
      </div>

      <div className="dashboard-content">
        {renderActiveSection()}
      </div>
    </div>
  );
};

export default AdminDashboard; 