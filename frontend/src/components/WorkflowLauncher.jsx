import React, { useCallback, useEffect, useRef, useState } from 'react';
import { startResearch, startUploadWorkflow } from '../services/api.js';

const CONFIG = {
  records: {
    eyebrow: 'Records workspace',
    title: 'Turn records into a review-ready timeline',
    description: 'Upload PDF or TXT exports. The pipeline extracts text, creates resumable chunks, validates structured summaries, classifies records, and builds lab-trend artefacts.',
    accept: '.pdf,.txt',
    formats: 'PDF or TXT',
    action: 'Process records',
  },
  imaging: {
    eyebrow: 'Imaging workspace',
    title: 'Organize DICOM studies for review',
    description: 'Convert readable DICOM pixels to enlarged PNGs, retain non-identifying study metadata, and create source-linked draft analysis reports.',
    accept: '.dcm,.dicom,.ima,application/dicom',
    formats: 'DICOM, DCM, or IMA',
    action: 'Process imaging',
  },
  genetics: {
    eyebrow: 'Genetics workspace',
    title: 'Inspect variants in a selected gene',
    description: 'Scan local VCF data for one human gene and annotate matching variants with current ClinVar evidence.',
    accept: '.vcf',
    formats: 'VCF',
    action: 'Analyze variants',
  },
  conditions: {
    eyebrow: 'Findings workspace',
    title: 'Roll up recurring non-normal findings',
    description: 'Combine report JSON files into a source-linked count of recurring findings while excluding normal or no-finding entries.',
    accept: '.json,application/json',
    formats: 'JSON reports',
    action: 'Build roll-up',
  },
};

function size(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 ** 2).toFixed(1)} MB`;
}

export function WorkflowLauncher({ type, onJobStarted }) {
  const config = CONFIG[type];
  const [files, setFiles] = useState([]);
  const [gene, setGene] = useState('');
  const [passOnly, setPassOnly] = useState(false);
  const [includeUnannotated, setIncludeUnannotated] = useState(true);
  const [dragging, setDragging] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const inputRef = useRef(null);

  useEffect(() => {
    setFiles([]);
    setError('');
  }, [type]);

  const add = useCallback((list) => {
    const incoming = Array.from(list || []);
    setFiles((current) => [...current, ...incoming]);
    setError('');
  }, []);

  useEffect(() => {
    if (type !== 'records') return undefined;
    const paste = (event) => {
      const pasted = Array.from(event.clipboardData?.files || []).filter((file) =>
        /\.(pdf|txt)$/i.test(file.name)
      );
      if (pasted.length) {
        event.preventDefault();
        add(pasted);
      }
    };
    window.addEventListener('paste', paste);
    return () => window.removeEventListener('paste', paste);
  }, [add, type]);

  const submit = async () => {
    if (!files.length || busy) return;
    if (type === 'genetics' && !gene.trim()) {
      setError('Enter a gene symbol, such as MSH2.');
      return;
    }
    setBusy(true);
    setError('');
    try {
      const options = type === 'genetics'
        ? { gene: gene.trim(), pass_only: passOnly, include_no_clinvar: includeUnannotated }
        : {};
      const result = await startUploadWorkflow(type, files, options);
      setFiles([]);
      onJobStarted(result.job_id);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="workspace-grid">
      <section className="workspace-intro">
        <span className="eyebrow">{config.eyebrow}</span>
        <h1>{config.title}</h1>
        <p>{config.description}</p>
        <div className="boundary-note">
          <span className="boundary-icon">i</span>
          <div><strong>Review support only</strong><br />Automated output is unverified and should be compared with its source.</div>
        </div>
      </section>

      <section className="panel upload-panel">
        <div className="panel-heading">
          <div><span className="step">01</span><h2>Add source files</h2></div>
          <span className="format-pill">{config.formats}</span>
        </div>

        {type === 'genetics' && (
          <div className="form-grid">
            <label className="field full"><span>Gene symbol</span><input value={gene} onChange={(e) => setGene(e.target.value.toUpperCase())} placeholder="e.g. MSH2" /></label>
            <label className="check"><input type="checkbox" checked={passOnly} onChange={(e) => setPassOnly(e.target.checked)} /> PASS variants only</label>
            <label className="check"><input type="checkbox" checked={includeUnannotated} onChange={(e) => setIncludeUnannotated(e.target.checked)} /> Include variants without ClinVar</label>
          </div>
        )}

        <div
          className={`dropzone ${dragging ? 'drag-over' : ''}`}
          onClick={() => inputRef.current?.click()}
          onDragOver={(event) => { event.preventDefault(); setDragging(true); }}
          onDragLeave={() => setDragging(false)}
          onDrop={(event) => { event.preventDefault(); setDragging(false); add(event.dataTransfer.files); }}
        >
          <div className="upload-mark">+</div>
          <strong>Drop files here or browse</strong>
          <span>{type === 'records' ? 'You can also paste files with Ctrl+V' : `Select one or more ${config.formats} files`}</span>
          <input ref={inputRef} hidden multiple type="file" accept={config.accept} onChange={(e) => { add(e.target.files); e.target.value = ''; }} />
        </div>

        {files.length > 0 && (
          <div className="file-stack">
            {files.map((file, index) => (
              <div className="file-row" key={`${file.name}-${index}`}>
                <span className="file-type">{file.name.split('.').pop()?.slice(0, 4).toUpperCase() || 'FILE'}</span>
                <div><strong>{file.name}</strong><span>{size(file.size)}</span></div>
                <button aria-label={`Remove ${file.name}`} onClick={() => setFiles((all) => all.filter((_, i) => i !== index))}>×</button>
              </div>
            ))}
          </div>
        )}

        {error && <div className="error-banner">{error}</div>}
        <button className="btn-primary action-btn" disabled={!files.length || busy} onClick={submit}>
          {busy ? 'Starting…' : config.action}<span>→</span>
        </button>
      </section>
    </div>
  );
}

export function ResearchLauncher({ onJobStarted }) {
  const [sourceType, setSourceType] = useState('rss');
  const [urls, setUrls] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const submit = async () => {
    const lines = urls.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
    if (!lines.length) { setError('Add at least one URL.'); return; }
    setBusy(true); setError('');
    try {
      const result = await startResearch(sourceType, lines);
      onJobStarted(result.job_id);
    } catch (err) { setError(err.message); }
    finally { setBusy(false); }
  };
  return (
    <div className="workspace-grid">
      <section className="workspace-intro">
        <span className="eyebrow">Research workspace</span>
        <h1>Bring the latest evidence into one place</h1>
        <p>Download RSS or Atom publications, or retrieve the full ClinicalTrials.gov history represented by saved RSS searches.</p>
        <div className="boundary-note"><span className="boundary-icon">↗</span><div><strong>Network connection required</strong><br />Incomplete downloads are reported as failures, never silent success.</div></div>
      </section>
      <section className="panel upload-panel">
        <div className="panel-heading"><div><span className="step">01</span><h2>Choose a source</h2></div></div>
        <div className="segmented">
          <button className={sourceType === 'rss' ? 'active' : ''} onClick={() => setSourceType('rss')}>RSS / Atom</button>
          <button className={sourceType === 'trials' ? 'active' : ''} onClick={() => setSourceType('trials')}>ClinicalTrials.gov</button>
        </div>
        <label className="field"><span>{sourceType === 'rss' ? 'Feed URLs' : 'ClinicalTrials.gov RSS search URLs'}</span><textarea rows="8" value={urls} onChange={(e) => setUrls(e.target.value)} placeholder="One https:// URL per line" /></label>
        {error && <div className="error-banner">{error}</div>}
        <button className="btn-primary action-btn" disabled={busy || !urls.trim()} onClick={submit}>{busy ? 'Downloading…' : 'Download research'}<span>→</span></button>
      </section>
    </div>
  );
}
