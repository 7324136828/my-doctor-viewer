import React, { useState, useEffect, useRef, useCallback } from 'react';
import { startConversion } from '../services/api.js';

function fmtSize(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

export function FileUpload({ onJobStarted }) {
  const [staged, setStaged] = useState([]);
  const [dragOver, setDragOver] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(null);
  const inputRef = useRef(null);
  const pasteCounter = useRef(0);

  const addFiles = useCallback((fileList) => {
    const incoming = Array.from(fileList || []);
    const pdfs = incoming.filter(
      (f) => f.type === 'application/pdf' || f.name.toLowerCase().endsWith('.pdf')
    );
    const rejected = incoming.length - pdfs.length;
    if (rejected > 0) {
      setError(`${rejected} non-PDF file(s) were ignored.`);
    } else {
      setError(null);
    }
    if (pdfs.length) {
      setStaged((prev) => [...prev, ...pdfs]);
    }
  }, []);

  // Clipboard paste (Ctrl+V) — picks up PDF file blobs.
  useEffect(() => {
    const handlePaste = (e) => {
      const items = e.clipboardData?.items;
      if (!items) return;
      const pasted = [];
      for (const item of items) {
        if (item.kind === 'file' && item.type === 'application/pdf') {
          const f = item.getAsFile();
          if (f) {
            pasteCounter.current += 1;
            pasted.push(
              new File([f], f.name && f.name !== 'blob' ? f.name : `pasted-${pasteCounter.current}.pdf`, {
                type: 'application/pdf',
              })
            );
          }
        }
      }
      if (pasted.length) {
        e.preventDefault();
        addFiles(pasted);
      }
    };
    window.addEventListener('paste', handlePaste);
    return () => window.removeEventListener('paste', handlePaste);
  }, [addFiles]);

  const onDrop = (e) => {
    e.preventDefault();
    setDragOver(false);
    addFiles(e.dataTransfer.files);
  };

  const removeFile = (idx) => {
    setStaged((prev) => prev.filter((_, i) => i !== idx));
  };

  const submit = async () => {
    if (!staged.length || submitting) return;
    setSubmitting(true);
    setError(null);
    try {
      const resp = await startConversion(staged);
      setStaged([]);
      onJobStarted(resp.job_id);
    } catch (err) {
      setError(err.message);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <section className="card">
      <h2>Convert Medical Record PDFs</h2>
      <p className="hint">
        Select, drag &amp; drop, or paste (Ctrl+V) PDF files. They are processed in an
        isolated system-temp folder and packaged into a downloadable ZIP.
      </p>

      <div
        className={dragOver ? 'dropzone drag-over' : 'dropzone'}
        onClick={() => inputRef.current?.click()}
        onDragOver={(e) => {
          e.preventDefault();
          setDragOver(true);
        }}
        onDragLeave={() => setDragOver(false)}
        onDrop={onDrop}
      >
        <div className="dropzone-inner">
          <div className="dropzone-icon">PDF</div>
          <div>Drop PDF files here, click to browse, or press Ctrl+V to paste</div>
        </div>
        <input
          ref={inputRef}
          type="file"
          accept=".pdf"
          multiple
          hidden
          onChange={(e) => {
            addFiles(e.target.files);
            e.target.value = '';
          }}
        />
      </div>

      {error && <div className="error-banner">{error}</div>}

      {staged.length > 0 && (
        <ul className="staged-list">
          {staged.map((f, i) => (
            <li key={`${f.name}-${i}`}>
              <span className="staged-name">{f.name}</span>
              <span className="staged-size">{fmtSize(f.size)}</span>
              <button className="btn-remove" onClick={() => removeFile(i)}>
                Remove
              </button>
            </li>
          ))}
        </ul>
      )}

      <button
        className="btn-primary"
        disabled={!staged.length || submitting}
        onClick={submit}
      >
        {submitting ? 'Uploading…' : `Convert ${staged.length || ''} PDF${staged.length === 1 ? '' : 's'}`}
      </button>
    </section>
  );
}
