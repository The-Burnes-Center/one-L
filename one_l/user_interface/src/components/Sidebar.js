/**
 * Collapsible Sidebar Navigation Component for One-L Application
 * Provides navigation to admin panel with expandable sections
 */

import React, { useState } from 'react';

const Sidebar = ({ activeSection, activeTab, onSectionChange, onTabChange }) => {
  const [isCollapsed, setIsCollapsed] = useState(false);
  const [adminExpanded, setAdminExpanded] = useState(activeSection === 'admin');

  const toggleSidebar = () => {
    setIsCollapsed(!isCollapsed);
  };

  const handleAdminClick = () => {
    if (activeSection !== 'admin') {
      onSectionChange('admin');
      setAdminExpanded(true);
    } else {
      setAdminExpanded(!adminExpanded);
    }
  };

  const handleMainClick = () => {
    onSectionChange('main');
    setAdminExpanded(false);
  };

  const adminTabs = [
    {
      id: 'data',
      label: 'Data',
      icon: '📊'
    }
    // Future admin tabs can be added here
  ];

  return (
    <div className={`sidebar ${isCollapsed ? 'collapsed' : ''}`}>
      <div className="sidebar-header">
        <div className="sidebar-brand">
          {!isCollapsed && (
            <>
              <h2>One-L</h2>
              <p>Document Management</p>
            </>
          )}
          {isCollapsed && <h2>1L</h2>}
        </div>
        <button className="sidebar-toggle" onClick={toggleSidebar}>
          <span className="toggle-icon">{isCollapsed ? '→' : '←'}</span>
        </button>
      </div>
      
      <nav className="sidebar-nav">
        {/* Main Section - Always visible, no tab appearance */}
        <button
          className={`nav-section ${activeSection === 'main' ? 'active' : ''}`}
          onClick={handleMainClick}
        >
          <span className="section-icon">🏠</span>
          {!isCollapsed && <span className="section-label">Home</span>}
        </button>

        {/* Admin Panel Section */}
        <div className="nav-section-group">
          <button
            className={`nav-section ${activeSection === 'admin' ? 'active' : ''}`}
            onClick={handleAdminClick}
          >
            <span className="section-icon">⚙️</span>
            {!isCollapsed && (
              <>
                <span className="section-label">Admin Panel</span>
                <span className={`expand-icon ${adminExpanded ? 'expanded' : ''}`}>▼</span>
              </>
            )}
          </button>
          
          {/* Admin Sub-tabs */}
          {!isCollapsed && adminExpanded && activeSection === 'admin' && (
            <div className="nav-subtabs">
              {adminTabs.map(tab => (
                <button
                  key={tab.id}
                  className={`nav-subtab ${activeTab === tab.id ? 'active' : ''}`}
                  onClick={() => onTabChange(tab.id)}
                >
                  <span className="subtab-icon">{tab.icon}</span>
                  <span className="subtab-label">{tab.label}</span>
                </button>
              ))}
            </div>
          )}
        </div>
      </nav>
      
      <div className="sidebar-footer">
        {!isCollapsed && <p className="version">v1.0.0</p>}
      </div>
    </div>
  );
};

export default Sidebar; 