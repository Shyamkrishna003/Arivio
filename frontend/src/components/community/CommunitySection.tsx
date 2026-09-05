import { useCallback, useEffect, useState } from 'react';
import { communityAPI } from '../../services/api';
import {
  Users, UserCheck, MessageSquarePlus, ThumbsUp, ThumbsDown, Flag,
  Info, Check, X, ChevronDown, ChevronUp, Trash2, ShieldAlert, Clock,
} from 'lucide-react';
import './CommunitySection.css';

interface Relevance {
  score: number;
  reasons: string[];
  differences: string[];
  not_compared: string[];
}

interface Review {
  id: number;
  product_id: number;
  usage_duration: string;
  experience_type: string;
  experience_text?: string | null;
  rating?: number | null;
  helpful_count: number;
  not_helpful_count: number;
  created_at: string;
  is_mine: boolean;
  is_published: boolean;
  moderation_note?: string | null;
  my_vote?: boolean | null;
  relevance?: Relevance | null;
}

interface BreakdownItem {
  experience_type: string;
  label: string;
  count: number;
  percentage: number;
}

interface Summary {
  total_experiences: number;
  breakdown: BreakdownItem[];
  average_rating?: number | null;
  relevant_experiences: number;
  relevant_breakdown: BreakdownItem[];
  disclaimer: string;
  insufficient_data: boolean;
}

interface CommunityData {
  product_id: number;
  summary: Summary;
  relevant_reviews: Review[];
  recent_reviews: Review[];
  my_review: Review | null;
}

const DURATIONS = [
  { value: 'once', label: 'Once' },
  { value: 'few_days', label: 'A few days' },
  { value: 'few_weeks', label: 'A few weeks' },
  { value: 'several_months', label: 'Several months' },
  { value: 'more_than_a_year', label: 'More than a year' },
];

const EXPERIENCES = [
  { value: 'no_noticeable_effect', label: 'No noticeable effect' },
  { value: 'positive', label: 'Positive' },
  { value: 'negative', label: 'Negative' },
  { value: 'digestive_discomfort', label: 'Digestive discomfort' },
  { value: 'skin_reaction', label: 'Skin reaction' },
  { value: 'headache', label: 'Headache' },
  { value: 'energy_change', label: 'Energy change' },
  { value: 'appetite_change', label: 'Appetite change' },
  { value: 'other', label: 'Other' },
];

const labelFor = (list: { value: string; label: string }[], value: string) =>
  list.find((i) => i.value === value)?.label || value.replace(/_/g, ' ');

// Experience types that describe a problem, so the bar can carry a colour
// without the component deciding what any of it means.
const ADVERSE = new Set([
  'negative', 'digestive_discomfort', 'skin_reaction', 'headache',
]);

function Breakdown({ items, total }: { items: BreakdownItem[]; total: number }) {
  if (!items.length) return null;
  return (
    <div className="cm-breakdown">
      {items.map((item) => (
        <div key={item.experience_type} className="cm-bar-row">
          <span className="cm-bar-label">{item.label}</span>
          <div className="cm-bar-track">
            <div
              className={`cm-bar-fill ${ADVERSE.has(item.experience_type) ? 'adverse' : ''}`}
              style={{ width: `${Math.max(item.percentage, 2)}%` }}
            />
          </div>
          {/* Counts are the honest unit at small n — a percentage off three
              reports reads as a finding. */}
          <span className="cm-bar-value">
            {total >= 5 ? `${item.percentage}%` : `${item.count}`}
          </span>
        </div>
      ))}
    </div>
  );
}

function ReviewCard({
  review, onVote, onFlag, onDelete,
}: {
  review: Review;
  onVote: (id: number, helpful: boolean) => void;
  onFlag: (id: number) => void;
  onDelete: (id: number) => void;
}) {
  const [showWhy, setShowWhy] = useState(false);
  const rel = review.relevance;

  return (
    <div className={`cm-review ${review.is_mine ? 'mine' : ''}`}>
      <div className="cm-review-head">
        <span className={`cm-chip ${ADVERSE.has(review.experience_type) ? 'adverse' : ''}`}>
          {labelFor(EXPERIENCES, review.experience_type)}
        </span>
        <span className="cm-meta">
          <Clock size={12} /> Used {labelFor(DURATIONS, review.usage_duration).toLowerCase()}
        </span>
        {review.is_mine && <span className="cm-chip mine-chip">Your experience</span>}
      </div>

      {/* Attribution is deliberately a similar *profile*, never a prediction
          about the reader. */}
      {rel && !review.is_mine && (
        <div className="cm-relevance">
          <button className="cm-why-toggle" onClick={() => setShowWhy((v) => !v)}>
            <UserCheck size={14} />
            <span>A user with a similar profile reported this</span>
            {showWhy ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
          </button>
          {showWhy && (
            <div className="cm-why">
              <p className="cm-why-title">Why this may be relevant to you</p>
              <ul className="cm-why-list">
                {rel.reasons.map((r, i) => (
                  <li key={i}><Check size={12} className="ok" /> {r}</li>
                ))}
              </ul>
              {rel.differences.length > 0 && (
                <>
                  <p className="cm-why-title">Potential differences</p>
                  <ul className="cm-why-list">
                    {rel.differences.map((d, i) => (
                      <li key={i}><X size={12} className="diff" /> {d}</li>
                    ))}
                  </ul>
                </>
              )}
              {rel.not_compared.length > 0 && (
                <p className="cm-why-note">
                  Not compared: {rel.not_compared.join(', ')}.
                </p>
              )}
            </div>
          )}
        </div>
      )}

      {review.experience_text && <p className="cm-text">{review.experience_text}</p>}

      {!review.is_published && review.is_mine && (
        <div className="cm-held">
          <ShieldAlert size={14} />
          <span>
            Held for review, so it isn't counted publicly yet.
            {review.moderation_note ? ` ${review.moderation_note}` : ''}
          </span>
        </div>
      )}

      <div className="cm-actions">
        {!review.is_mine ? (
          <>
            <button
              className={`cm-action ${review.my_vote === true ? 'active' : ''}`}
              onClick={() => onVote(review.id, true)}
            >
              <ThumbsUp size={13} /> Helpful {review.helpful_count > 0 && `(${review.helpful_count})`}
            </button>
            <button
              className={`cm-action ${review.my_vote === false ? 'active' : ''}`}
              onClick={() => onVote(review.id, false)}
            >
              <ThumbsDown size={13} /> {review.not_helpful_count > 0 && `(${review.not_helpful_count})`}
            </button>
            <button className="cm-action subtle" onClick={() => onFlag(review.id)}>
              <Flag size={13} /> Report
            </button>
          </>
        ) : (
          <button className="cm-action subtle" onClick={() => onDelete(review.id)}>
            <Trash2 size={13} /> Remove
          </button>
        )}
      </div>
    </div>
  );
}

export default function CommunitySection({
  productId, isAuthenticated,
}: { productId: number; isAuthenticated: boolean }) {
  const [data, setData] = useState<CommunityData | null>(null);
  const [loading, setLoading] = useState(true);
  const [showForm, setShowForm] = useState(false);
  const [error, setError] = useState('');

  const [duration, setDuration] = useState('few_weeks');
  const [experience, setExperience] = useState('no_noticeable_effect');
  const [text, setText] = useState('');
  const [shareContext, setShareContext] = useState(true);
  const [submitting, setSubmitting] = useState(false);

  const load = useCallback(async () => {
    try {
      const res = await communityAPI.getForProduct(productId);
      setData(res.data);
    } catch {
      // The aggregate is a supporting panel; a failure here must not take the
      // product page with it.
      setData(null);
    } finally {
      setLoading(false);
    }
  }, [productId]);

  useEffect(() => { load(); }, [load]);

  useEffect(() => {
    if (data?.my_review) {
      setDuration(data.my_review.usage_duration);
      setExperience(data.my_review.experience_type);
      setText(data.my_review.experience_text || '');
    }
  }, [data?.my_review]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    setSubmitting(true);
    try {
      await communityAPI.submitReview({
        product_id: productId,
        usage_duration: duration,
        experience_type: experience,
        experience_text: text.trim() || undefined,
        share_context: shareContext,
      });
      setShowForm(false);
      await load();
    } catch (err: any) {
      setError(err?.response?.data?.detail || 'Could not save your experience.');
    } finally {
      setSubmitting(false);
    }
  };

  const handleVote = async (id: number, helpful: boolean) => {
    try { await communityAPI.vote(id, helpful); await load(); }
    catch { /* a failed vote should not disturb the page */ }
  };

  const handleFlag = async (id: number) => {
    try {
      const res = await communityAPI.flag(id);
      setError(res.data?.withdrawn_pending_moderation
        ? 'Reported. This experience has been withdrawn pending moderation.'
        : 'Reported. Thanks — a moderator will take a look.');
      await load();
    } catch (err: any) {
      setError(err?.response?.data?.detail || 'Could not report this experience.');
    }
  };

  const handleDelete = async (id: number) => {
    try { await communityAPI.removeReview(id); await load(); }
    catch { /* ignore */ }
  };

  if (loading) return null;

  const summary = data?.summary;
  const total = summary?.total_experiences ?? 0;
  const relevant = data?.relevant_reviews ?? [];
  const recent = data?.recent_reviews ?? [];

  return (
    <div className="community-section card animate-fade-in-up">
      <div className="section-header">
        <h2><Users size={20} /> Community Experiences</h2>
        <p className="text-sm text-muted mt-1">
          What people report after actually using this product.
        </p>
      </div>

      {error && <div className="cm-notice">{error}</div>}

      {/* Community data starts at zero and grows only from real contributions —
          an empty product says so rather than implying a consensus. */}
      {total === 0 ? (
        <div className="cm-empty">
          <MessageSquarePlus size={28} className="cm-empty-icon" />
          <p className="cm-empty-title">No experiences shared yet</p>
          <p className="cm-empty-desc">
            Nothing here is generated — this fills up only as real people report how
            a product went for them. Be the first.
          </p>
        </div>
      ) : (
        <>
          {/* "People like you" is kept visually and numerically distinct from
              the overall figures. */}
          {isAuthenticated && (summary?.relevant_experiences ?? 0) > 0 && (
            <div className="cm-block cm-block-personal">
              <div className="cm-block-head">
                <h3><UserCheck size={16} /> People Like You</h3>
                <span className="cm-count">
                  {summary!.relevant_experiences} of {total} relevant to your profile
                </span>
              </div>
              <Breakdown items={summary!.relevant_breakdown} total={summary!.relevant_experiences} />
            </div>
          )}

          <div className="cm-block">
            <div className="cm-block-head">
              <h3><Users size={16} /> Overall</h3>
              <span className="cm-count">
                {total} {total === 1 ? 'experience' : 'experiences'}
                {summary?.average_rating ? ` · avg ${summary.average_rating}/5` : ''}
              </span>
            </div>
            <Breakdown items={summary?.breakdown ?? []} total={total} />
            {summary?.insufficient_data && (
              <p className="cm-thin">
                Too few reports to show meaningful percentages — raw counts are shown instead.
              </p>
            )}
          </div>

          {/* Stated with every aggregate, not tucked away once at the bottom. */}
          <p className="cm-disclaimer">
            <Info size={13} /> {summary?.disclaimer}
          </p>
        </>
      )}

      {isAuthenticated && (
        <div className="cm-contribute">
          {!showForm ? (
            <button className="btn btn-secondary" onClick={() => setShowForm(true)}>
              <MessageSquarePlus size={16} />
              {data?.my_review ? 'Update your experience' : 'Share your experience'}
            </button>
          ) : (
            <form className="cm-form" onSubmit={handleSubmit}>
              <div className="cm-field">
                <label>How long did you use it?</label>
                <select className="input" value={duration} onChange={(e) => setDuration(e.target.value)}>
                  {DURATIONS.map((d) => <option key={d.value} value={d.value}>{d.label}</option>)}
                </select>
              </div>

              <div className="cm-field">
                <label>What did you experience?</label>
                <select className="input" value={experience} onChange={(e) => setExperience(e.target.value)}>
                  {EXPERIENCES.map((x) => <option key={x.value} value={x.value}>{x.label}</option>)}
                </select>
              </div>

              <div className="cm-field">
                <label>Describe it (optional)</label>
                <textarea
                  className="input cm-textarea"
                  rows={3}
                  placeholder="What happened, and over what period?"
                  value={text}
                  onChange={(e) => setText(e.target.value)}
                  maxLength={4000}
                />
              </div>

              <label className="cm-consent">
                <input
                  type="checkbox"
                  checked={shareContext}
                  onChange={(e) => setShareContext(e.target.checked)}
                />
                <span>
                  Share anonymised context (age range, diet, goals) so this can be matched
                  to similar users. Your identity is never attached, and only what your
                  privacy settings already allow is shared.
                </span>
              </label>

              <div className="cm-form-actions">
                <button type="submit" className="btn btn-primary" disabled={submitting}>
                  {submitting ? 'Saving...' : 'Share experience'}
                </button>
                <button type="button" className="btn btn-secondary" onClick={() => setShowForm(false)}>
                  Cancel
                </button>
              </div>
            </form>
          )}
        </div>
      )}

      {relevant.length > 0 && (
        <div className="cm-list">
          <h4 className="cm-list-title"><UserCheck size={15} /> Most relevant to you</h4>
          {relevant.map((r) => (
            <ReviewCard key={r.id} review={r} onVote={handleVote} onFlag={handleFlag} onDelete={handleDelete} />
          ))}
        </div>
      )}

      {recent.length > 0 && (
        <div className="cm-list">
          <h4 className="cm-list-title">
            <Users size={15} /> {relevant.length > 0 ? 'Other experiences' : 'Recent experiences'}
          </h4>
          {recent.map((r) => (
            <ReviewCard key={r.id} review={r} onVote={handleVote} onFlag={handleFlag} onDelete={handleDelete} />
          ))}
        </div>
      )}

      {!isAuthenticated && total > 0 && (
        <p className="cm-signed-out">
          Sign in to see which of these experiences come from people with a profile like yours.
        </p>
      )}
    </div>
  );
}
