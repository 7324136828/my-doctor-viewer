import React from 'react';
import { artifactUrl } from '../services/api.js';

export function ResultsPanel({ job, downloadUrl }) {
  const summary = job.result?.summary || {};
  const artifacts = job.result?.artifacts || [];
  return (
    <div className="results-panel">
      <div className="result-head"><div><span className="success-check">✓</span><h3>Review package ready</h3></div><a className="btn-primary" href={downloadUrl} download>Download ZIP</a></div>
      <div className="safety-banner"><strong>Unverified output</strong><span>{job.result?.safety_notice || 'Review automated output against its source before relying on it.'}</span></div>
      {Object.keys(summary).length > 0 && <div className="summary-grid">{Object.entries(summary).map(([key, value]) => <div key={key}><span>{key.replaceAll('_', ' ')}</span><strong>{String(value ?? '—')}</strong></div>)}</div>}
      {artifacts.length > 0 && <div className="artifact-list"><h4>Files in this package</h4>{artifacts.map((item) => <a key={item.name} href={artifactUrl(job.id, item.name)} target="_blank" rel="noreferrer"><span>{item.name}</span><small>{item.size ? `${(item.size / 1024).toFixed(1)} KB` : ''} ↗</small></a>)}</div>}
    </div>
  );
}
