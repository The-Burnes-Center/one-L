/**
 * Centralized job polling service
 * Provides a single source of truth for polling job status across the application
 * 
 * Polling phases (20 min total, ~104 API calls):
 *   Phase 1: 0-3 min   → 15 sec interval (~12 calls)
 *   Phase 2: 3-15 min  → 10 sec interval (~72 calls)
 *   Phase 3: 15-20 min → 15 sec interval (~20 calls)
 */

import { agentAPI } from './api.js';

class JobPollingService {
  constructor() {
    this.activePolls = new Map(); // jobId -> { timeoutId, callbacks, lastStatus, startTime }
    
    // Polling phase configuration (times in milliseconds)
    this.phases = [
      { endTime: 3 * 60 * 1000, interval: 15000 },   // 0-3 min: 15 sec
      { endTime: 15 * 60 * 1000, interval: 10000 },  // 3-15 min: 10 sec
      { endTime: 20 * 60 * 1000, interval: 15000 }   // 15-20 min: 15 sec
    ];
    
    this.maxDuration = 20 * 60 * 1000; // 20 minutes max
  }

  /**
   * Get the appropriate poll interval based on elapsed time
   * @param {number} elapsedTime - Time elapsed since polling started (ms)
   * @returns {number} Poll interval in milliseconds
   */
  getIntervalForElapsedTime(elapsedTime) {
    for (const phase of this.phases) {
      if (elapsedTime < phase.endTime) {
        return phase.interval;
      }
    }
    // Default to last phase interval if somehow past all phases
    return this.phases[this.phases.length - 1].interval;
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

    const startTime = Date.now();
    const callbacks = {
      onUpdate: onUpdate ? [onUpdate] : [],
      onComplete: onComplete ? [onComplete] : [],
      onError: onError ? [onError] : []
    };

    const scheduleNextPoll = () => {
      const pollData = this.activePolls.get(jobId);
      if (!pollData) return; // Polling was stopped
      
      const elapsedTime = Date.now() - pollData.startTime;
      const interval = this.getIntervalForElapsedTime(elapsedTime);
      
      pollData.timeoutId = setTimeout(poll, interval);
    };

    const poll = async () => {
      try {
        const pollData = this.activePolls.get(jobId);
        if (!pollData) return; // Polling was stopped
        
        const elapsedTime = Date.now() - pollData.startTime;
        
        // Stop if max duration reached
        if (elapsedTime > this.maxDuration) {
          console.warn(`JobPollingService: Max duration (20 min) reached for job ${jobId}`);
          this.stopPolling(jobId);
          return;
        }

        const statusResponse = await agentAPI.getJobStatus(jobId);

        if (!statusResponse.success) {
          console.warn(`JobPollingService: Failed to get status for job ${jobId}`);
          scheduleNextPoll();
          return;
        }

        const status = statusResponse.status || 'processing';

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
        } else {
          // Schedule next poll for non-terminal states
          scheduleNextPoll();
        }

        // Update last status
        if (this.activePolls.has(jobId)) {
          this.activePolls.get(jobId).lastStatus = statusResponse;
        }

      } catch (error) {
        console.error(`JobPollingService: Error polling job ${jobId}:`, error);
        // Don't stop polling on transient errors, schedule next poll
        scheduleNextPoll();
      }
    };

    this.activePolls.set(jobId, {
      timeoutId: null,
      callbacks,
      lastStatus: null,
      startTime
    });

    // Start polling immediately, then schedule subsequent polls dynamically
    poll();

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
        if (poll.timeoutId) clearTimeout(poll.timeoutId);
        this.activePolls.delete(jobId);
      }
      return;
    }

    // Otherwise, stop completely
    if (poll.timeoutId) clearTimeout(poll.timeoutId);
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
   * @param {number} maxDurationMs - Maximum polling duration in ms (default: 20 min)
   * @returns {Promise<Object>} Final status response
   */
  async pollUntilComplete(jobId, onUpdate = null, maxDurationMs = 20 * 60 * 1000) {
    return new Promise((resolve, reject) => {
      const startTime = Date.now();
      let timeoutId = null;
      
      const scheduleNextPoll = () => {
        const elapsedTime = Date.now() - startTime;
        const interval = this.getIntervalForElapsedTime(elapsedTime);
        timeoutId = setTimeout(poll, interval);
      };
      
      const poll = async () => {
        try {
          const elapsedTime = Date.now() - startTime;
          
          if (elapsedTime > maxDurationMs) {
            reject(new Error(`Max polling duration (${maxDurationMs / 60000} min) reached for job ${jobId}`));
            return;
          }

          const statusResponse = await this.getStatus(jobId);
          
          if (!statusResponse.success) {
            scheduleNextPoll(); // Continue polling on transient errors
            return;
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
            resolve(statusResponse);
          } else {
            scheduleNextPoll();
          }
        } catch (error) {
          if (timeoutId) clearTimeout(timeoutId);
          reject(error);
        }
      };
      
      // Start immediately
      poll();
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
      if (poll.timeoutId) clearTimeout(poll.timeoutId);
    });
    this.activePolls.clear();
  }
}

// Export singleton instance
const jobPollingService = new JobPollingService();
export default jobPollingService;

