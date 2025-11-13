/**
 * Metrics Dashboard Component for One-L Application
 * Displays system-wide metrics for administrators
 */

import React, { useState, useEffect } from 'react';
import { sessionAPI } from '../services/api';
import './MetricsDashboard.css';

const MetricsDashboard = () => {
  const [metrics, setMetrics] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [lastUpdated, setLastUpdated] = useState(null);

  useEffect(() => {
    loadMetrics();
    // Refresh metrics every 5 minutes
    const interval = setInterval(loadMetrics, 5 * 60 * 1000);
    return () => clearInterval(interval);
  }, []);

  const loadMetrics = async () => {
    try {
      setLoading(true);
      setError(null);
      const response = await sessionAPI.getAdminMetrics();
      
      if (response.success && response.metrics) {
        setMetrics(response.metrics);
        setLastUpdated(new Date());
      } else {
        setError(response.error || 'Failed to load metrics');
      }
    } catch (err) {
      console.error('Error loading metrics:', err);
      setError(err.message || 'Failed to load metrics');
    } finally {
      setLoading(false);
    }
  };

  const formatNumber = (num) => {
    if (num === null || num === undefined) return '0';
    return num.toLocaleString();
  };

  const formatPercentage = (num) => {
    if (num === null || num === undefined) return '0%';
    return `${num.toFixed(1)}%`;
  };

  const formatDuration = (minutes) => {
    if (minutes === null || minutes === undefined) return '0 min';
    if (minutes < 60) return `${Math.round(minutes)} min`;
    const hours = Math.floor(minutes / 60);
    const mins = Math.round(minutes % 60);
    return `${hours}h ${mins}m`;
  };

  if (loading && !metrics) {
    return (
      <div className="metrics-dashboard">
        <div className="metrics-loading">
          <p>Loading metrics...</p>
        </div>
      </div>
    );
  }

  if (error && !metrics) {
    return (
      <div className="metrics-dashboard">
        <div className="metrics-error">
          <p>Error loading metrics: {error}</p>
          <button onClick={loadMetrics} className="retry-button">Retry</button>
        </div>
      </div>
    );
  }

  if (!metrics) {
    return (
      <div className="metrics-dashboard">
        <div className="metrics-error">
          <p>No metrics available</p>
        </div>
      </div>
    );
  }

  return (
    <div className="metrics-dashboard">
      <div className="metrics-header">
        <h2>System Metrics</h2>
        <div className="metrics-controls">
          {lastUpdated && (
            <span className="last-updated">
              Last updated: {lastUpdated.toLocaleTimeString()}
            </span>
          )}
          <button onClick={loadMetrics} className="refresh-button" disabled={loading}>
            {loading ? 'Refreshing...' : 'Refresh'}
          </button>
        </div>
      </div>

      <div className="metrics-grid">
        {/* Sessions Section */}
        <div className="metric-card">
          <h3>Sessions</h3>
          <div className="metric-stats">
            <div className="metric-stat">
              <span className="metric-label">Total Sessions</span>
              <span className="metric-value">{formatNumber(metrics.sessions?.total)}</span>
            </div>
            <div className="metric-stat">
              <span className="metric-label">Active (24h)</span>
              <span className="metric-value highlight">{formatNumber(metrics.sessions?.active)}</span>
            </div>
            <div className="metric-stat">
              <span className="metric-label">With Results</span>
              <span className="metric-value">{formatNumber(metrics.sessions?.with_results)}</span>
            </div>
            <div className="metric-stat">
              <span className="metric-label">Without Results</span>
              <span className="metric-value">{formatNumber(metrics.sessions?.without_results)}</span>
            </div>
            <div className="metric-stat">
              <span className="metric-label">Avg Duration</span>
              <span className="metric-value">{formatDuration(metrics.sessions?.average_duration_minutes)}</span>
            </div>
          </div>
        </div>

        {/* Users Section */}
        <div className="metric-card">
          <h3>Users</h3>
          <div className="metric-stats">
            <div className="metric-stat">
              <span className="metric-label">Total Users</span>
              <span className="metric-value">{formatNumber(metrics.users?.total)}</span>
            </div>
            {metrics.users?.top_users && metrics.users.top_users.length > 0 && (
              <div className="metric-subsection">
                <h4>Top Users (by sessions)</h4>
                <div className="top-users-list">
                  {metrics.users.top_users.slice(0, 5).map((user, index) => (
                    <div key={index} className="top-user-item">
                      <span className="user-id">{user.user_id.substring(0, 20)}...</span>
                      <span className="user-count">{user.session_count} sessions</span>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        </div>

        {/* Documents Section */}
        <div className="metric-card">
          <h3>Documents</h3>
          <div className="metric-stats">
            <div className="metric-stat">
              <span className="metric-label">Total Processed</span>
              <span className="metric-value">{formatNumber(metrics.documents?.total_processed)}</span>
            </div>
            <div className="metric-stat">
              <span className="metric-label">With Redlines</span>
              <span className="metric-value highlight">{formatNumber(metrics.documents?.with_redlines)}</span>
            </div>
            <div className="metric-stat">
              <span className="metric-label">Without Redlines</span>
              <span className="metric-value">{formatNumber(metrics.documents?.without_redlines)}</span>
            </div>
            <div className="metric-stat">
              <span className="metric-label">Redline Rate</span>
              <span className="metric-value highlight">{formatPercentage(metrics.documents?.redline_percentage)}</span>
            </div>
          </div>
        </div>

        {/* Redlines Section */}
        <div className="metric-card">
          <h3>Redlines</h3>
          <div className="metric-stats">
            <div className="metric-stat">
              <span className="metric-label">Total Redlines</span>
              <span className="metric-value highlight">{formatNumber(metrics.redlines?.total)}</span>
            </div>
            <div className="metric-stat">
              <span className="metric-label">Avg per Document</span>
              <span className="metric-value">{formatNumber(metrics.redlines?.average_per_document)}</span>
            </div>
          </div>
        </div>

        {/* Activity Section */}
        <div className="metric-card">
          <h3>Recent Activity (7 days)</h3>
          <div className="metric-stats">
            <div className="metric-stat">
              <span className="metric-label">New Sessions</span>
              <span className="metric-value">{formatNumber(metrics.activity?.sessions_last_7_days)}</span>
            </div>
            <div className="metric-stat">
              <span className="metric-label">Documents Analyzed</span>
              <span className="metric-value highlight">{formatNumber(metrics.activity?.analyses_last_7_days)}</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};

export default MetricsDashboard;

