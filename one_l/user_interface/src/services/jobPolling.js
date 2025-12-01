/**
 * Centralized job polling service
 * Provides a single source of truth for polling job status across the application
 */

import { agentAPI } from './api.js';

class JobPollingService {
  constructor() {
    this.activePolls = new Map(); // jobId -> { intervalId, callbacks, lastStatus }
    this.pollInterval = 5000; // 5 seconds
    this.maxAttempts = 120; // 10 minutes max
  }

  /**
   * Start polling a job
   * @param {string} jobId - The job ID to poll
   * @param {Function} onUpdate - Callback when status updates (receives statusResponse)
   * @param {Function} onComplete - Callback when job completes (receives statusResponse)
   * @param {Function} onError - Callback when job fails (receives statusResponse)
   * @returns {Function} Function to stop polling
   */
  startPolling(jobId, { onUpdate, onComplete, onError } = {}) {
    if (!jobId) {
      console.warn('JobPollingService: Cannot start polling without jobId');
      return () => {};
    }

    // If already polling this job, just add callbacks
    if (this.activePolls.has(jobId)) {
      const existing = this.activePolls.get(jobId);
      if (onUpdate) existing.callbacks.onUpdate.push(onUpdate);
      if (onComplete) existing.callbacks.onComplete.push(onComplete);
      if (onError) existing.callbacks.onError.push(onError);
      return () => this.stopPolling(jobId, { onUpdate, onComplete, onError });
    }

    let attempts = 0;
    const callbacks = {
      onUpdate: onUpdate ? [onUpdate] : [],
      onComplete: onComplete ? [onComplete] : [],
      onError: onError ? [onError] : []
    };

    const poll = async () => {
      try {
        attempts++;
        
        // Stop if max attempts reached
        if (attempts > this.maxAttempts) {
          console.warn(`JobPollingService: Max attempts reached for job ${jobId}`);
          this.stopPolling(jobId);
          return;
        }

        const statusResponse = await agentAPI.getJobStatus(jobId);

        if (!statusResponse.success) {
          console.warn(`JobPollingService: Failed to get status for job ${jobId}`);
          return;
        }

        const status = statusResponse.status || 'processing';
        const lastStatus = this.activePolls.get(jobId)?.lastStatus;

        // Always call onUpdate if provided
        if (callbacks.onUpdate.length > 0) {
          callbacks.onUpdate.forEach(cb => {
            try {
              cb(statusResponse);
            } catch (error) {
              console.error('JobPollingService: Error in onUpdate callback:', error);
            }
          });
        }

        // Check for terminal states
        if (status === 'completed') {
          callbacks.onComplete.forEach(cb => {
            try {
              cb(statusResponse);
            } catch (error) {
              console.error('JobPollingService: Error in onComplete callback:', error);
            }
          });
          this.stopPolling(jobId);
        } else if (status === 'failed') {
          callbacks.onError.forEach(cb => {
            try {
              cb(statusResponse);
            } catch (error) {
              console.error('JobPollingService: Error in onError callback:', error);
            }
          });
          this.stopPolling(jobId);
        }

        // Update last status
        if (this.activePolls.has(jobId)) {
          this.activePolls.get(jobId).lastStatus = statusResponse;
        }

      } catch (error) {
        console.error(`JobPollingService: Error polling job ${jobId}:`, error);
        // Don't stop polling on transient errors, but log them
      }
    };

    // Start polling immediately, then at intervals
    poll();
    const intervalId = setInterval(poll, this.pollInterval);

    this.activePolls.set(jobId, {
      intervalId,
      callbacks,
      lastStatus: null,
      startTime: Date.now()
    });

    // Return stop function
    return () => this.stopPolling(jobId, { onUpdate, onComplete, onError });
  }

  /**
   * Stop polling a job
   * @param {string} jobId - The job ID to stop polling
   * @param {Object} callbacksToRemove - Optional callbacks to remove instead of stopping entirely
   */
  stopPolling(jobId, callbacksToRemove = null) {
    if (!this.activePolls.has(jobId)) {
      return;
    }

    const poll = this.activePolls.get(jobId);

    // If removing specific callbacks, just remove those
    if (callbacksToRemove) {
      if (callbacksToRemove.onUpdate) {
        poll.callbacks.onUpdate = poll.callbacks.onUpdate.filter(
          cb => cb !== callbacksToRemove.onUpdate
        );
      }
      if (callbacksToRemove.onComplete) {
        poll.callbacks.onComplete = poll.callbacks.onComplete.filter(
          cb => cb !== callbacksToRemove.onComplete
        );
      }
      if (callbacksToRemove.onError) {
        poll.callbacks.onError = poll.callbacks.onError.filter(
          cb => cb !== callbacksToRemove.onError
        );
      }
      // If no callbacks left, stop polling
      if (poll.callbacks.onUpdate.length === 0 && 
          poll.callbacks.onComplete.length === 0 && 
          poll.callbacks.onError.length === 0) {
        clearInterval(poll.intervalId);
        this.activePolls.delete(jobId);
      }
      return;
    }

    // Otherwise, stop completely
    clearInterval(poll.intervalId);
    this.activePolls.delete(jobId);
  }

  /**
   * Get current status for a job (without starting polling)
   * @param {string} jobId - The job ID
   * @returns {Promise<Object>} Status response
   */
  async getStatus(jobId) {
    try {
      return await agentAPI.getJobStatus(jobId);
    } catch (error) {
      console.error(`JobPollingService: Error getting status for job ${jobId}:`, error);
      return { success: false, error: error.message };
    }
  }

  /**
   * Poll a job until completion (returns promise that resolves when done)
   * Useful for awaitable polling patterns
   * @param {string} jobId - The job ID
   * @param {Function} onUpdate - Optional callback for progress updates
   * @param {number} maxAttempts - Maximum polling attempts (default: 120)
   * @returns {Promise<Object>} Final status response
   */
  async pollUntilComplete(jobId, onUpdate = null, maxAttempts = 120) {
    return new Promise(async (resolve, reject) => {
      let attempts = 0;
      let pollInterval = null;
      
      const poll = async () => {
        try {
          attempts++;
          if (attempts > maxAttempts) {
            if (pollInterval) clearInterval(pollInterval);
            reject(new Error(`Max polling attempts (${maxAttempts}) reached for job ${jobId}`));
            return;
          }

          const statusResponse = await this.getStatus(jobId);
          
          if (!statusResponse.success) {
            return; // Continue polling on transient errors
          }

          const status = statusResponse.status || 'processing';
          
          // Call update callback if provided
          if (onUpdate) {
            try {
              onUpdate(statusResponse);
            } catch (error) {
              console.error('JobPollingService: Error in onUpdate callback:', error);
            }
          }

          // Check for terminal states
          if (status === 'completed' || status === 'failed') {
            if (pollInterval) clearInterval(pollInterval);
            resolve(statusResponse);
          }
        } catch (error) {
          if (pollInterval) clearInterval(pollInterval);
          reject(error);
        }
      };
      
      // Start immediately, then poll at intervals
      poll();
      pollInterval = setInterval(poll, this.pollInterval);
    });
  }

  /**
   * Get all actively polled jobs
   * @returns {Array<string>} Array of job IDs
   */
  getActiveJobs() {
    return Array.from(this.activePolls.keys());
  }

  /**
   * Stop all polling
   */
  stopAll() {
    this.activePolls.forEach((poll, jobId) => {
      clearInterval(poll.intervalId);
    });
    this.activePolls.clear();
  }
}

// Export singleton instance
const jobPollingService = new JobPollingService();
export default jobPollingService;

