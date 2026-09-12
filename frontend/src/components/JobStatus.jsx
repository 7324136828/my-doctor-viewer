import React, { useState, useEffect } from 'react';
import { getJob, downloadZipUrl } from '../services/api.js';
import { LogViewer } from './LogViewer.jsx';
import { ResultsPanel } from './ResultsPanel.jsx';

const ACTIVE = new Set(['queued', 'in_progress']);

export function JobStatus({ jobId }) {
  const [job, setJob] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    setJob(null);
    setError(null);
    let cancelled = false;
    let timer = null;

    const poll = async () => {
      try {
        const j = await getJob(jobId);
        if (cancelled) return;
        setJob(j);
        if (ACTIVE.has(j.status)) {
          timer = setTimeout(poll, 1500);
        }
      } catch (err) {
        if (!cancelled) setError(err.message);
      }
    };
    poll();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [jobId]);

  if (error) {
    return <div className="panel error-banner">Could not load job: {error}</div>;
  }
  if (!job) {
    return <div className="panel">Loading job…</div>;
  }

  const active = ACTIVE.has(job.status);

  return (
    <section className="panel job-panel">
      <div className="panel-heading"><div><span className="step">02</span><h2>Job progress</h2></div><span className={`badge badge-${job.status}`}>{job.status.replace('_', ' ')}</span></div>
      <div className="job-meta">
        <span className="job-type-label">{job.job_type}</span>
        <span className="job-files">{job.filenames?.join(', ') || job.filename}</span>
      </div>

      <div className="progress-bar">
        <div
          className={`progress-fill ${active ? 'animated' : ''}`}
          style={{ width: `${job.progress}%` }}
        />
      </div>
      <div className="progress-label">{job.progress}%</div>

      {job.error_message && (
        <div className="error-banner">{job.error_message}</div>
      )}

      <LogViewer jobId={jobId} active={active} />

      {job.status === 'completed' && (
        <ResultsPanel job={job} downloadUrl={downloadZipUrl(jobId)} />
      )}
    </section>
  );
}
