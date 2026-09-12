import React, { useState, useEffect, useRef } from 'react';
import { getJobLogs } from '../services/api.js';

export function LogViewer({ jobId, active }) {
  const [logs, setLogs] = useState('');
  const [open, setOpen] = useState(false);
  const boxRef = useRef(null);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    const load = () =>
      getJobLogs(jobId)
        .then((d) => {
          if (!cancelled) setLogs(d.logs);
        })
        .catch(() => {});
    load();
    const timer = active ? setInterval(load, 2000) : null;
    return () => {
      cancelled = true;
      if (timer) clearInterval(timer);
    };
  }, [jobId, open, active]);

  useEffect(() => {
    if (boxRef.current) {
      boxRef.current.scrollTop = boxRef.current.scrollHeight;
    }
  }, [logs]);

  return (
    <div className="log-viewer">
      <button className="btn-link" onClick={() => setOpen((o) => !o)}>
        {open ? 'Hide logs' : 'Show logs'}
      </button>
      {open && (
        <pre ref={boxRef} className="log-box">
          {logs || '(no logs yet)'}
        </pre>
      )}
    </div>
  );
}
