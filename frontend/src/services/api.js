const API_BASE = '';

async function request(path, options = {}) {
  const resp = await fetch(`${API_BASE}${path}`, options);
  if (!resp.ok) {
    let detail = `HTTP ${resp.status}`;
    try {
      const body = await resp.json();
      detail = body.detail || detail;
    } catch {
      /* non-JSON error body */
    }
    throw new Error(detail);
  }
  return resp.json();
}

export function health() {
  return request('/api/health');
}

export function getJobs() {
  return request('/api/jobs');
}

export function getJob(jobId) {
  return request(`/api/jobs/${jobId}`);
}

export function getJobLogs(jobId) {
  return request(`/api/jobs/${jobId}/logs`);
}

export function startConversion(files) {
  return startUploadWorkflow('records', files);
}

export function startUploadWorkflow(type, files, options = {}) {
  const form = new FormData();
  for (const f of files) {
    form.append('files', f, f.name);
  }
  for (const [key, value] of Object.entries(options)) {
    form.append(key, String(value));
  }
  return request(`/api/workflows/${type}`, { method: 'POST', body: form });
}

export function startResearch(sourceType, urls) {
  return request('/api/workflows/research', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ source_type: sourceType, urls }),
  });
}

export function discardJob(jobId) {
  return request(`/api/jobs/${jobId}/discard`, { method: 'POST' });
}

export function resumeJob(jobId) {
  return request(`/api/jobs/${jobId}/resume`, { method: 'POST' });
}

export function downloadZipUrl(jobId) {
  return `${API_BASE}/api/jobs/${jobId}/download-zip`;
}

export function artifactUrl(jobId, name) {
  return `${API_BASE}/api/jobs/${jobId}/artifacts/${name.split('/').map(encodeURIComponent).join('/')}`;
}
