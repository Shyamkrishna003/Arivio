import { useState } from 'react';
import { healthAPI } from '../../services/api';
import {
  Activity, AlertTriangle, Check, FileText, Loader2, Lock, ShieldCheck,
  Trash2, Upload, X,
} from 'lucide-react';
import './HealthSection.css';

export interface HealthMarker {
  label: string;
  value: number | null;
  unit: string | null;
  ref_low: number | null;
  ref_high: number | null;
  measured_at: string | null;
  confirmed: boolean;
  analyte: string | null;
  flag: string;
  recognized: boolean;
  unit_converted: boolean;
  note: string | null;
}

export interface HealthContext {
  enabled: boolean;
  consent_given: boolean;
  documents: Array<{
    id: number;
    title: string;
    marker_count: number;
    extraction_source: string;
    confirmed: boolean;
    uploaded_at: string;
  }>;
  conditions: Array<{
    key: string;
    label: string;
    severity: string;
    explanation: string;
    triggered_by: string[];
    watch_nutrients: string[];
    prefer_nutrients: string[];
  }>;
  goal_conflicts: Array<{
    goal_label: string;
    condition_label: string;
    nutrients: string[];
    description: string;
  }>;
  unavailable_reason?: string | null;
}

interface Props {
  context: HealthContext | null;
  onChange: () => void;
}

const FLAG_LABEL: Record<string, string> = {
  high: 'High', low: 'Low', normal: 'Normal', unknown: 'Not assessed',
};

export default function HealthSection({ context, onChange }: Props) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [confirmingWithdraw, setConfirmingWithdraw] = useState(false);

  // The document currently open for review. Markers are only editable here —
  // nothing reaches the scoring engine until this form is saved.
  const [editing, setEditing] = useState<{ id: number; title: string } | null>(null);
  const [markers, setMarkers] = useState<HealthMarker[]>([]);
  const [warnings, setWarnings] = useState<string[]>([]);

  if (!context) return null;

  if (!context.enabled) {
    return (
      <div className="profile-section profile-section--wide card">
        <div className="section-header">
          <h2><Activity size={20} /> Health Context</h2>
        </div>
        <p className="health-disabled">
          <Lock size={15} /> {context.unavailable_reason ||
            'Health documents are unavailable on this server.'}
        </p>
      </div>
    );
  }

  const handleConsent = async (consent: boolean) => {
    setBusy(true);
    setError('');
    try {
      await healthAPI.setConsent(consent);
      setConfirmingWithdraw(false);
      setEditing(null);
      onChange();
    } catch (err: any) {
      setError(err.response?.data?.detail || 'Could not update your consent.');
    } finally {
      setBusy(false);
    }
  };

  const handleUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = '';
    if (!file) return;

    setBusy(true);
    setError('');
    try {
      const { data } = await healthAPI.upload(file);
      setEditing({ id: data.document.id, title: data.document.title });
      setMarkers(data.document.markers);
      setWarnings(data.warnings || []);
      onChange();
    } catch (err: any) {
      setError(err.response?.data?.detail || 'Could not read that document.');
    } finally {
      setBusy(false);
    }
  };

  const openDocument = async (id: number, title: string) => {
    setBusy(true);
    setError('');
    try {
      const { data } = await healthAPI.getDocument(id);
      setEditing({ id, title });
      setMarkers(data.markers);
      setWarnings(data.warnings || []);
    } catch {
      setError('Could not open that document.');
    } finally {
      setBusy(false);
    }
  };

  const saveMarkers = async () => {
    if (!editing) return;
    setBusy(true);
    setError('');
    try {
      await healthAPI.confirm(editing.id, { title: editing.title, markers });
      setEditing(null);
      onChange();
    } catch (err: any) {
      setError(err.response?.data?.detail || 'Could not save these readings.');
    } finally {
      setBusy(false);
    }
  };

  const deleteDocument = async (id: number) => {
    setBusy(true);
    try {
      await healthAPI.remove(id);
      if (editing?.id === id) setEditing(null);
      onChange();
    } catch {
      setError('Could not delete that document.');
    } finally {
      setBusy(false);
    }
  };

  const setMarker = (index: number, patch: Partial<HealthMarker>) => {
    setMarkers((prev) => prev.map((m, i) => (i === index ? { ...m, ...patch } : m)));
  };

  const addMarker = () => {
    setMarkers((prev) => [...prev, {
      label: '', value: null, unit: null, ref_low: null, ref_high: null,
      measured_at: null, confirmed: true, analyte: null, flag: 'unknown',
      recognized: false, unit_converted: false, note: null,
    }]);
  };

  // ── Consent gate ──
  if (!context.consent_given) {
    return (
      <div className="profile-section profile-section--wide card">
        <div className="section-header">
          <h2><Activity size={20} /> Health Context</h2>
        </div>
        <div className="consent-gate">
          <p className="consent-lead">
            Upload a blood test and we'll take your results into account when
            scoring products — flagging things that work against your readings,
            and telling you when one of your goals pulls the other way.
          </p>
          <ul className="consent-terms">
            <li>
              <ShieldCheck size={15} />
              <span><strong>The document is never stored.</strong> It's read once, and only the
                numbers you confirm are kept.</span>
            </li>
            <li>
              <ShieldCheck size={15} />
              <span><strong>Encrypted and private to you.</strong> Health data is never shared
                with the community, at any setting.</span>
            </li>
            <li>
              <ShieldCheck size={15} />
              <span><strong>Deleting means deleting.</strong> Withdraw consent and everything is
                removed immediately.</span>
            </li>
            <li>
              <AlertTriangle size={15} />
              <span><strong>This is not medical advice.</strong> ARIVIO doesn't diagnose. Talk to
                your doctor before changing your diet.</span>
            </li>
          </ul>
          <p className="consent-note">
            Reading a document sends its contents to the AI provider configured
            for this server.
          </p>
          {error && <div className="form-error"><AlertTriangle size={14} /> {error}</div>}
          <button className="btn btn-primary" disabled={busy}
            onClick={() => handleConsent(true)}>
            {busy ? 'Saving…' : 'I understand — enable health context'}
          </button>
        </div>
      </div>
    );
  }

  // ── Marker review ──
  if (editing) {
    return (
      <div className="profile-section profile-section--wide card">
        <div className="section-header">
          <h2><FileText size={20} /> Check your results</h2>
          <button className="btn-icon" onClick={() => setEditing(null)} aria-label="Close">
            <X size={18} />
          </button>
        </div>

        <p className="review-lead">
          Tick only the readings you want us to use. Anything left unticked is
          stored but never affects a product score.
        </p>

        {warnings.length > 0 && (
          <div className="health-warnings">
            <AlertTriangle size={16} />
            <ul>{warnings.map((w, i) => <li key={i}>{w}</li>)}</ul>
          </div>
        )}
        {error && <div className="form-error"><AlertTriangle size={14} /> {error}</div>}

        <label className="field">
          <span>Document name</span>
          <input className="input" value={editing.title} maxLength={255}
            onChange={(e) => setEditing({ ...editing, title: e.target.value })} />
        </label>

        <div className="markers-table">
          <div className="marker-row marker-head">
            <span>Use</span><span>Test</span><span>Value</span><span>Unit</span><span>Status</span><span />
          </div>
          {markers.map((m, i) => (
            <div className={`marker-row ${m.recognized ? '' : 'is-unrecognized'}`} key={i}>
              <input type="checkbox" checked={m.confirmed}
                aria-label={`Use ${m.label || 'this reading'}`}
                onChange={(e) => setMarker(i, { confirmed: e.target.checked })} />
              <input className="input input-sm" value={m.label} placeholder="e.g. HbA1c"
                onChange={(e) => setMarker(i, { label: e.target.value })} />
              <input className="input input-sm" type="number" step="any"
                value={m.value ?? ''}
                onChange={(e) => setMarker(i, {
                  value: e.target.value === '' ? null : Number(e.target.value),
                })} />
              <input className="input input-sm" value={m.unit ?? ''} placeholder="mg/dL"
                onChange={(e) => setMarker(i, { unit: e.target.value || null })} />
              <span className={`marker-flag flag-${m.flag}`}>{FLAG_LABEL[m.flag] || m.flag}</span>
              <button className="btn-icon" aria-label="Remove reading"
                onClick={() => setMarkers((p) => p.filter((_, j) => j !== i))}>
                <Trash2 size={15} />
              </button>
            </div>
          ))}
          {markers.length === 0 && (
            <p className="empty-text">No readings yet — add them from your report.</p>
          )}
        </div>

        <p className="marker-note">
          Greyed rows are tests we don't have guidance for. They're kept for your
          records but never scored.
        </p>

        <div className="review-actions">
          <button className="btn btn-secondary" onClick={addMarker}>Add a reading</button>
          <button className="btn btn-primary" onClick={saveMarkers} disabled={busy}>
            {busy ? 'Saving…' : <>Save &amp; apply <Check size={15} /></>}
          </button>
        </div>
      </div>
    );
  }

  // ── Overview ──
  return (
    <div className="profile-section profile-section--wide card">
      <div className="section-header">
        <h2><Activity size={20} /> Health Context</h2>
        <label className="btn btn-secondary btn-sm cursor-pointer">
          {busy ? <Loader2 size={15} className="spin" /> : <Upload size={15} />}
          {busy ? ' Reading…' : ' Upload report'}
          <input type="file" accept="application/pdf,image/*" style={{ display: 'none' }}
            disabled={busy} onChange={handleUpload} />
        </label>
      </div>

      {error && <div className="form-error"><AlertTriangle size={14} /> {error}</div>}

      {context.conditions.length > 0 && (
        <div className="conditions-list">
          {context.conditions.map((c) => (
            <div key={c.key} className={`condition-card severity-${c.severity}`}>
              <h4>{c.label}</h4>
              <p className="condition-explanation">{c.explanation}</p>
              <div className="condition-meta">
                <span className="condition-trigger">From: {c.triggered_by.join('; ')}</span>
                {c.watch_nutrients.length > 0 && (
                  <span className="condition-nutrients">
                    Watching: {c.watch_nutrients.join(', ')}
                  </span>
                )}
                {c.prefer_nutrients.length > 0 && (
                  <span className="condition-nutrients">
                    Looking for: {c.prefer_nutrients.join(', ')}
                  </span>
                )}
              </div>
            </div>
          ))}
          <p className="health-disclaimer">
            <AlertTriangle size={14} /> These are patterns in the numbers you
            uploaded, not diagnoses. ARIVIO can't assess your health — please
            talk to your doctor or a dietitian.
          </p>
        </div>
      )}

      <div className="documents-list">
        {context.documents.map((d) => (
          <div className="document-row" key={d.id}>
            <FileText size={16} className="doc-icon" />
            <div className="doc-text">
              <span className="doc-title">{d.title}</span>
              <span className="doc-meta">
                {d.marker_count} reading{d.marker_count === 1 ? '' : 's'} ·{' '}
                {new Date(d.uploaded_at).toLocaleDateString()}
                {!d.confirmed && <em className="doc-pending"> · needs review</em>}
              </span>
            </div>
            <button className="btn btn-secondary btn-sm" disabled={busy}
              onClick={() => openDocument(d.id, d.title)}>
              {d.confirmed ? 'Edit' : 'Review'}
            </button>
            <button className="btn-icon" aria-label={`Delete ${d.title}`}
              disabled={busy} onClick={() => deleteDocument(d.id)}>
              <Trash2 size={15} />
            </button>
          </div>
        ))}
        {context.documents.length === 0 && (
          <p className="empty-text">
            No reports yet. Upload a blood test (PDF or a photo) to get started.
          </p>
        )}
      </div>

      <div className="health-footer">
        {confirmingWithdraw ? (
          <div className="withdraw-confirm">
            <span>
              This permanently deletes {context.documents.length} document
              {context.documents.length === 1 ? '' : 's'} and all readings. Continue?
            </span>
            <button className="btn btn-danger btn-sm" disabled={busy}
              onClick={() => handleConsent(false)}>Delete everything</button>
            <button className="btn btn-secondary btn-sm"
              onClick={() => setConfirmingWithdraw(false)}>Cancel</button>
          </div>
        ) : (
          <button className="btn-link-muted" onClick={() => setConfirmingWithdraw(true)}>
            Withdraw consent and delete my health data
          </button>
        )}
      </div>
    </div>
  );
}
