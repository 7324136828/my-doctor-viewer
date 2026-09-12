import React, { useEffect, useState } from 'react';
import { discardJob, downloadZipUrl, getJobs, resumeJob } from '../services/api.js';

function fmtSize(bytes) {
  if (!bytes && bytes !== 0) return '—';
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 ** 2).toFixed(1)} MB`;
}

export function ConversionHistory({ onContinueJob }) {
  const [jobs, setJobs] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const loadJobs = async () => {
    try { setJobs(await getJobs()); setError(''); }
    catch (err) { setError(err.message); }
    finally { setLoading(false); }
  };
  useEffect(() => { loadJobs(); const timer = setInterval(loadJobs, 4000); return () => clearInterval(timer); }, []);
  const discard = async (id) => {
    if (!window.confirm('Permanently purge this job’s temporary files?')) return;
    try { await discardJob(id); await loadJobs(); } catch (err) { setError(err.message); }
  };
  const resume = async (id) => {
    try { await resumeJob(id); onContinueJob(id); await loadJobs(); } catch (err) { setError(err.message); }
  };
  return (
    <section className="history-page">
      <div className="history-heading"><div><span className="eyebrow">Job history</span><h1>Activity</h1><p>Inspect, resume, download, or purge work from this local session.</p></div><button className="refresh-btn" onClick={loadJobs}>↻ Refresh</button></div>
      {error && <div className="error-banner">{error}</div>}
      <div className="panel history-panel">
        {loading ? <p>Loading activity…</p> : jobs.length === 0 ? <div className="empty-state"><span>◇</span><h3>No jobs yet</h3><p>Your processing history will appear here.</p></div> : (
          <div className="table-scroll"><table className="jobs-table"><thead><tr><th>Workspace</th><th>Source</th><th>Size</th><th>Status</th><th>Created</th><th>Actions</th></tr></thead><tbody>
            {jobs.map((job) => <tr key={job.id}>
              <td><span className="job-type-label">{job.job_type}</span></td>
              <td className="source-cell" title={job.filenames?.join(', ')}>{job.filenames?.join(', ') || job.filename}</td>
              <td>{fmtSize(job.file_size)}</td>
              <td><span className={`badge badge-${job.status}`}>{['queued', 'in_progress'].includes(job.status) ? `${job.status.replace('_', ' ')} ${job.progress}%` : job.status}</span></td>
              <td>{new Date(job.created_at).toLocaleString()}</td>
              <td><div className="btn-group">
                {job.status !== 'discarded' && <button className="btn-continue" onClick={() => onContinueJob(job.id)}>Inspect</button>}
                {job.status === 'failed' && <button className="btn-continue" onClick={() => resume(job.id)}>Resume</button>}
                {job.status === 'completed' && <a className="btn-download" href={downloadZipUrl(job.id)} download>ZIP</a>}
                {job.status !== 'discarded' && <button className="btn-discard" onClick={() => discard(job.id)}>Purge</button>}
              </div></td>
            </tr>)}
          </tbody></table></div>
        )}
      </div>
    </section>
  );
}
