import { useState, useEffect, useCallback } from 'react';
import { useParams, useNavigate, Link } from 'react-router-dom';
import { ocrAPI, resolveImageUrl } from '../../services/api';
import {
  AlertTriangle, ArrowRight, Check, Eye, Package, ScanLine, Sparkles,
} from 'lucide-react';
import './LabelConfirm.css';

interface Extraction {
  label_type: string;
  name: string | null;
  brand: string | null;
  category: string | null;
  serving_size: string | null;
  barcode: string | null;
  ingredients: string[];
  nutrition: Record<string, number | null>;
  confidence: number;
  warnings: string[];
  provider: string;
  model: string;
}

interface Match {
  id: number;
  name: string;
  brand?: string;
  category?: string;
  image_url?: string;
  match_score: number;
}

// Order and labels for the nutrition panel. Per 100g throughout — the basis
// the scoring engine's thresholds are calibrated for.
const NUTRIENTS: [string, string][] = [
  ['energy_kcal', 'Energy (kcal)'],
  ['protein_g', 'Protein (g)'],
  ['total_fat_g', 'Total fat (g)'],
  ['saturated_fat_g', 'Saturated fat (g)'],
  ['trans_fat_g', 'Trans fat (g)'],
  ['total_carbohydrates_g', 'Carbohydrates (g)'],
  ['total_sugars_g', 'Sugars (g)'],
  ['added_sugars_g', 'Added sugars (g)'],
  ['dietary_fiber_g', 'Fibre (g)'],
  ['sodium_mg', 'Sodium (mg)'],
  ['cholesterol_mg', 'Cholesterol (mg)'],
];

export default function LabelConfirm() {
  const { extractionId = '' } = useParams();
  const navigate = useNavigate();

  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [extraction, setExtraction] = useState<Extraction | null>(null);
  const [matches, setMatches] = useState<Match[]>([]);
  // Near-identical products the server refused to duplicate. Distinct from
  // `matches`: those are suggestions shown up front, these are a block the
  // user has to resolve before the product can be created.
  const [duplicates, setDuplicates] = useState<Match[] | null>(null);
  const [imageUrl, setImageUrl] = useState<string | null>(null);

  // The editable form. Seeded from the extraction, then owned by the user —
  // PRD §14 requires OCR errors to be correctable before anything is analyzed.
  const [form, setForm] = useState({
    name: '', brand: '', category: '', serving_size: '', barcode: '',
    ingredients_text: '',
  });
  const [nutrition, setNutrition] = useState<Record<string, string>>({});

  useEffect(() => {
    let cancelled = false;

    const load = async () => {
      try {
        const { data } = await ocrAPI.getExtraction(extractionId);
        if (cancelled) return;

        const ex: Extraction = data.extraction;
        setExtraction(ex);
        setMatches(data.matches || []);
        setImageUrl(data.image_url || null);
        setForm({
          name: ex.name || '',
          brand: ex.brand || '',
          category: ex.category || '',
          serving_size: ex.serving_size || '',
          barcode: ex.barcode || '',
          ingredients_text: ex.ingredients.join(', '),
        });
        setNutrition(
          Object.fromEntries(
            NUTRIENTS.map(([key]) => [
              key,
              ex.nutrition?.[key] != null ? String(ex.nutrition[key]) : '',
            ])
          )
        );
      } catch (err: any) {
        if (!cancelled) {
          setError(
            err.response?.status === 404
              ? 'This scan has expired. Please photograph the label again.'
              : 'Could not load that scan.'
          );
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    };

    if (extractionId) load();
    return () => { cancelled = true; };
  }, [extractionId]);

  const setField = useCallback((key: string, value: string) => {
    setForm((prev) => ({ ...prev, [key]: value }));
  }, []);

  const submit = async (force: boolean) => {
    if (!form.name.trim()) {
      setError('A product name is required.');
      return;
    }

    setSaving(true);
    setError('');
    setDuplicates(null);
    try {
      // Blank fields are sent as null, not "": the product should record that
      // a value was not read, rather than that it is an empty string.
      const numeric: Record<string, number | null> = {};
      for (const [key] of NUTRIENTS) {
        const raw = (nutrition[key] || '').trim();
        const parsed = raw === '' ? null : Number(raw);
        numeric[key] = parsed !== null && Number.isFinite(parsed) ? parsed : null;
      }

      const { data } = await ocrAPI.confirm({
        extraction_id: extractionId,
        name: form.name.trim(),
        brand: form.brand.trim() || null,
        category: form.category.trim() || null,
        serving_size: form.serving_size.trim() || null,
        barcode: form.barcode.trim() || null,
        ingredients_text: form.ingredients_text.trim() || null,
        nutrition: numeric,
        force,
      });
      navigate(`/products/${data.id}`);
    } catch (err: any) {
      const detail = err.response?.data?.detail;
      // 409 means the catalogue already holds something with almost this name.
      // Offered rather than enforced: only the person holding the packet can
      // say whether two similar names are the same product.
      if (err.response?.status === 409 && detail?.matches) {
        setDuplicates(detail.matches);
        setError(detail.message);
      } else {
        setError(
          (typeof detail === 'string' ? detail : null) ||
          'Could not save this product. Please try again.'
        );
      }
      setSaving(false);
    }
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    submit(false);
  };

  if (loading) {
    return (
      <div className="confirm-page container">
        <div className="loading-state">
          <div className="spinner"></div>
          <p>Loading your scan...</p>
        </div>
      </div>
    );
  }

  if (!extraction) {
    return (
      <div className="confirm-page container">
        <div className="empty-state card">
          <AlertTriangle size={48} className="empty-icon" />
          <h3>Scan unavailable</h3>
          <p>{error || 'We could not find that scan.'}</p>
          <Link to="/scan" className="btn btn-primary mt-4">Scan again</Link>
        </div>
      </div>
    );
  }

  const confidencePct = Math.round(extraction.confidence * 100);
  const readByModel = extraction.provider !== 'none' && extraction.provider !== 'tesseract';

  return (
    <div className="confirm-page container">
      <div className="confirm-header animate-fade-in-up">
        <h1>Check what we read</h1>
        <p>
          These details came off your photo and may contain mistakes. Correct anything
          that's wrong — <strong>they'll be used to check this product against your
          allergies and goals.</strong>
        </p>
      </div>

      {/* Anything the reader was unsure about, before the form rather than after:
          a warning below the fold is a warning nobody reads. */}
      {(extraction.warnings.length > 0 || !readByModel) && (
        <div className="confirm-warnings animate-fade-in-up">
          <AlertTriangle size={18} />
          <ul>
            {extraction.warnings.map((w, i) => <li key={i}>{w}</li>)}
            {extraction.ingredients.length === 0 && (
              <li>
                No ingredient list was read. Without one we can't check this product
                for allergens — add it below if it's printed on the pack.
              </li>
            )}
          </ul>
        </div>
      )}

      {matches.length > 0 && (
        <div className="confirm-matches card animate-fade-in-up">
          <h3><Package size={18} /> Is it one of these?</h3>
          <p className="matches-hint">
            We already have these — picking one avoids creating a duplicate and gives
            you better data than a photo can.
          </p>
          <div className="matches-list">
            {matches.map((m) => (
              <Link to={`/products/${m.id}`} key={m.id} className="match-row">
                <div className="match-thumb">
                  {m.image_url
                    ? <img src={resolveImageUrl(m.image_url)} alt="" />
                    : <Package size={18} className="placeholder-icon" />}
                </div>
                <div className="match-text">
                  <span className="match-name">{m.name}</span>
                  {m.brand && <span className="match-brand">{m.brand}</span>}
                </div>
                <span className="match-score">{Math.round(m.match_score * 100)}% match</span>
                <ArrowRight size={16} />
              </Link>
            ))}
          </div>
        </div>
      )}

      <form onSubmit={handleSubmit} className="confirm-layout animate-fade-in-up stagger-1">
        <aside className="confirm-image-col">
          {imageUrl && (
            <div className="confirm-image card">
              <img src={resolveImageUrl(imageUrl)} alt="The label you photographed" />
            </div>
          )}
          <div className="confirm-meta card">
            <div className="meta-row">
              <span><Eye size={14} /> Read confidence</span>
              <strong className={confidencePct >= 70 ? 'text-good' : 'text-warn'}>
                {confidencePct}%
              </strong>
            </div>
            <div className="meta-row">
              <span><ScanLine size={14} /> Label type</span>
              <strong>{extraction.label_type}</strong>
            </div>
            <div className="meta-row">
              <span><Sparkles size={14} /> Read by</span>
              <strong>{readByModel ? extraction.model : 'basic OCR'}</strong>
            </div>
            <p className="meta-note">
              Saved as user-submitted and unverified, because it came from a photo.
            </p>
          </div>
        </aside>

        <div className="confirm-fields">
          {error && (
            <div className="confirm-error"><AlertTriangle size={16} /> {error}</div>
          )}

          <section className="field-group card">
            <h3>Product</h3>
            <label className="field">
              <span>Name <em>required</em></span>
              <input
                className="input" value={form.name} required maxLength={500}
                onChange={(e) => setField('name', e.target.value)}
                placeholder="As printed on the pack"
              />
            </label>
            <div className="field-row">
              <label className="field">
                <span>Brand</span>
                <input className="input" value={form.brand} maxLength={255}
                  onChange={(e) => setField('brand', e.target.value)} />
              </label>
              <label className="field">
                <span>Category</span>
                <input className="input" value={form.category} maxLength={255}
                  onChange={(e) => setField('category', e.target.value)} />
              </label>
            </div>
            <div className="field-row">
              <label className="field">
                <span>Serving size</span>
                <input className="input" value={form.serving_size} maxLength={100}
                  placeholder="e.g. 30 g"
                  onChange={(e) => setField('serving_size', e.target.value)} />
              </label>
              <label className="field">
                <span>Barcode</span>
                <input className="input" value={form.barcode} maxLength={255}
                  inputMode="numeric"
                  onChange={(e) => setField('barcode', e.target.value)} />
              </label>
            </div>
          </section>

          <section className="field-group card">
            <h3>Ingredients</h3>
            <p className="field-hint">
              Comma-separated, in the order printed on the pack — that order means
              most first, and the analysis relies on it.
            </p>
            <textarea
              className="input textarea" rows={5}
              value={form.ingredients_text}
              placeholder="wheat flour, sugar, palm oil, cocoa butter"
              onChange={(e) => setField('ingredients_text', e.target.value)}
            />
          </section>

          <section className="field-group card">
            <h3>Nutrition <span className="per-100">per 100g</span></h3>
            <p className="field-hint">
              Leave anything blank that isn't printed. A guessed number is worse than
              a missing one.
            </p>
            <div className="nutrition-grid">
              {NUTRIENTS.map(([key, label]) => (
                <label className="field" key={key}>
                  <span>{label}</span>
                  <input
                    className="input" type="number" step="any" min="0"
                    value={nutrition[key] ?? ''}
                    onChange={(e) =>
                      setNutrition((prev) => ({ ...prev, [key]: e.target.value }))
                    }
                  />
                </label>
              ))}
            </div>
          </section>

          {duplicates && duplicates.length > 0 && (
            <div className="duplicate-block card">
              <h3><AlertTriangle size={18} /> Is this one of these?</h3>
              <p>
                Adding it again would split its reviews and scores across two
                entries. Open the existing one if it's the same product.
              </p>
              <div className="matches-list">
                {duplicates.map((d) => (
                  <Link to={`/products/${d.id}`} key={d.id} className="match-row">
                    <div className="match-thumb">
                      {d.image_url
                        ? <img src={resolveImageUrl(d.image_url)} alt="" />
                        : <Package size={18} className="placeholder-icon" />}
                    </div>
                    <div className="match-text">
                      <span className="match-name">{d.name}</span>
                      {d.brand && <span className="match-brand">{d.brand}</span>}
                    </div>
                    <span className="match-score">{Math.round(d.match_score * 100)}% match</span>
                    <ArrowRight size={16} />
                  </Link>
                ))}
              </div>
              <button type="button" className="btn btn-secondary btn-sm"
                disabled={saving} onClick={() => submit(true)}>
                {saving ? 'Saving…' : 'No — this is a different product, add it'}
              </button>
            </div>
          )}

          <div className="confirm-actions">
            <Link to="/scan" className="btn btn-secondary">Scan again</Link>
            <button type="submit" className="btn btn-primary" disabled={saving}>
              {saving ? 'Saving...' : <>Confirm & analyze <Check size={16} /></>}
            </button>
          </div>
        </div>
      </form>
    </div>
  );
}
