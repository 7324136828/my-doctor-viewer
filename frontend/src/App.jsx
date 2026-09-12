import React, { useCallback, useEffect, useState } from 'react';
import { ConversionHistory } from './components/ConversionHistory.jsx';
import { JobStatus } from './components/JobStatus.jsx';
import { ResearchLauncher, WorkflowLauncher } from './components/WorkflowLauncher.jsx';
import { health } from './services/api.js';

const NAV = [
  { id: 'home', label: 'Overview', icon: '⌂' },
  { id: 'records', label: 'Medical records', icon: '▤' },
  { id: 'imaging', label: 'MRI & DICOM', icon: '◉' },
  { id: 'genetics', label: 'Genetics', icon: '⌁' },
  { id: 'conditions', label: 'Condition roll-up', icon: '≋' },
  { id: 'research', label: 'Research feeds', icon: '↗' },
  { id: 'history', label: 'Activity', icon: '◷' },
];

const MODULES = [
  { id: 'records', kicker: 'Organize', title: 'Medical records', text: 'Convert PDFs and text exports into structured, classified summaries and lab trends.', stat: 'PDF · TXT', tone: 'mint' },
  { id: 'imaging', kicker: 'Review', title: 'MRI & DICOM', text: 'Create viewable images and source-linked draft reports without stopping on bad files.', stat: 'DICOM', tone: 'blue' },
  { id: 'genetics', kicker: 'Explore', title: 'Genetics', text: 'Find gene-specific variants in a local VCF and add ClinVar evidence.', stat: 'VCF · ClinVar', tone: 'violet' },
  { id: 'conditions', kicker: 'Summarize', title: 'Condition roll-up', text: 'See recurring non-normal findings and every report they came from.', stat: 'JSON', tone: 'sand' },
  { id: 'research', kicker: 'Discover', title: 'Research feeds', text: 'Save publication feeds and complete trial-search histories for offline review.', stat: 'RSS · CT.gov', tone: 'rose' },
];

const SCREEN_IDS = new Set(NAV.map((item) => item.id));

function screenFromHash() {
  const candidate = window.location.hash.replace(/^#\/?/, '');
  return SCREEN_IDS.has(candidate) ? candidate : 'home';
}

function Overview({ navigate, llm }) {
  return (
    <div className="overview">
      <section className="hero">
        <div>
          <span className="eyebrow">Private, local-first health workspace</span>
          <h1>Your records, made easier<br />to review and discuss.</h1>
          <p>Organize complex health files into traceable, review-ready artefacts. Your uploaded files stay in isolated temporary job folders on this computer.</p>
          <button className="btn-primary hero-action" onClick={() => navigate('records')}>Start with records <span>→</span></button>
        </div>
        <div className="hero-card">
          <div className="pulse-ring"><div className="pulse-core">MD</div></div>
          <div className="hero-status"><span className={llm?.llm_enabled ? 'dot on' : 'dot'} />{llm?.llm_enabled ? 'MedGemma ready' : 'Deterministic mode'}</div>
          <p>{llm?.llm_enabled ? 'Local AI summaries are enabled.' : 'Source organization works without a model.'}</p>
        </div>
      </section>

      <section className="module-section">
        <div className="section-title"><div><span className="eyebrow">Workspaces</span><h2>What would you like to review?</h2></div><button className="text-button" onClick={() => navigate('history')}>View activity →</button></div>
        <div className="module-grid">
          {MODULES.map((module) => (
            <button className={`module-card ${module.tone}`} key={module.id} onClick={() => navigate(module.id)}>
              <span className="module-kicker">{module.kicker}</span>
              <h3>{module.title}</h3>
              <p>{module.text}</p>
              <div><span>{module.stat}</span><b>→</b></div>
            </button>
          ))}
        </div>
      </section>

      <section className="trust-strip">
        <div><strong>Source-linked</strong><span>Findings point back to original files</span></div>
        <div><strong>Resumable</strong><span>Checkpoints preserve completed work</span></div>
        <div><strong>Clearly unverified</strong><span>AI output is labeled for human review</span></div>
      </section>
    </div>
  );
}

export default function App() {
  const [screen, setScreen] = useState(screenFromHash);
  const [activeJobId, setActiveJobId] = useState(null);
  const [llm, setLlm] = useState(null);
  const [menuOpen, setMenuOpen] = useState(false);

  useEffect(() => { health().then(setLlm).catch(() => setLlm({ status: 'down' })); }, []);
  useEffect(() => {
    const syncHash = () => { setScreen(screenFromHash()); setActiveJobId(null); };
    window.addEventListener('hashchange', syncHash);
    return () => window.removeEventListener('hashchange', syncHash);
  }, []);
  const navigate = useCallback((next) => {
    window.location.hash = next;
    setScreen(next);
    setActiveJobId(null);
    setMenuOpen(false);
  }, []);
  const handleJobStarted = useCallback((jobId) => { setActiveJobId(jobId); }, []);
  const handleContinueJob = useCallback((jobId) => { setActiveJobId(jobId); }, []);

  return (
    <div className="app-shell">
      <aside className={`sidebar ${menuOpen ? 'open' : ''}`}>
        <button className="wordmark" onClick={() => navigate('home')}><span>+</span><div>My Doctor<small>Viewer</small></div></button>
        <nav>
          {NAV.map((item, index) => (
            <React.Fragment key={item.id}>
              {index === 1 && <span className="nav-section">Workspaces</span>}
              {index === 6 && <span className="nav-section">Jobs</span>}
              <button className={screen === item.id ? 'active' : ''} onClick={() => navigate(item.id)}><span>{item.icon}</span>{item.label}</button>
            </React.Fragment>
          ))}
        </nav>
        <div className="sidebar-footer">
          <div className="privacy-mark">⌾</div>
          <div><strong>Local workspace</strong><span>Temporary files expire automatically</span></div>
        </div>
      </aside>

      <div className="main-shell">
        <header className="mobile-header"><button onClick={() => setMenuOpen((open) => !open)}>☰</button><strong>My Doctor Viewer</strong><span className={llm?.llm_enabled ? 'dot on' : 'dot'} /></header>
        <main className="page">
          {screen === 'home' && <Overview navigate={navigate} llm={llm} />}
          {['records', 'imaging', 'genetics', 'conditions'].includes(screen) && <WorkflowLauncher type={screen} onJobStarted={handleJobStarted} />}
          {screen === 'research' && <ResearchLauncher onJobStarted={handleJobStarted} />}
          {screen === 'history' && <ConversionHistory onContinueJob={handleContinueJob} />}
          {activeJobId && <JobStatus jobId={activeJobId} />}
        </main>
      </div>
    </div>
  );
}
