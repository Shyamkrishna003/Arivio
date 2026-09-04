import { useState, useEffect } from 'react';
import { useSelector } from 'react-redux';
import type { RootState } from '../../store';
import { profileAPI } from '../../services/api';
import { Shield, Target, AlertTriangle, CheckCircle2, HeartPulse, Loader2, Lock, UserCircle } from 'lucide-react';
import './Profile.css';

interface Opt { value: string; label: string }

export default function Profile() {
  const { user } = useSelector((state: RootState) => state.auth);
  const [profile, setProfile] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [success] = useState('');

  // Form states
  const [newGoal, setNewGoal] = useState('');
  const [newAllergy, setNewAllergy] = useState('');
  const [newAllergyType, setNewAllergyType] = useState('allergy');
  const [newAllergySeverity, setNewAllergySeverity] = useState('severe');
  const [knownAllergens, setKnownAllergens] = useState<string[]>([]);
  // e.g. rejected as a duplicate of a goal/allergy the user already has —
  // surfaced so "Add" doesn't silently do nothing.
  const [goalError, setGoalError] = useState('');
  const [allergyError, setAllergyError] = useState('');
  const [privacy, setPrivacy] = useState<Record<string, boolean> | null>(null);
  const [options, setOptions] = useState<{
    age_ranges: Opt[]; activity_levels: Opt[]; dietary_patterns: Opt[]; preferences: Opt[];
  } | null>(null);
  const [detailsSaved, setDetailsSaved] = useState('');

  useEffect(() => {
    fetchProfile();
    // Canonical names for autocomplete — picking one of these gets full synonym
    // coverage, where free text may only match literally.
    profileAPI.knownAllergens()
      .then((r) => setKnownAllergens(r.data))
      .catch(() => setKnownAllergens([]));
    profileAPI.getPrivacy()
      .then((r) => setPrivacy(r.data))
      .catch(() => setPrivacy(null));
    profileAPI.getOptions()
      .then((r) => setOptions(r.data))
      .catch(() => setOptions(null));
  }, []);

  // Saves per field. These feed both the suitability score and community
  // relevance, so a half-filled form abandoned at a Save button is worse than
  // banking each answer as it is given.
  const saveDetail = async (patch: Record<string, string>) => {
    setProfile((p: any) => ({ ...p, profile: { ...(p?.profile || {}), ...patch } }));
    try {
      await profileAPI.update(patch);
      setDetailsSaved('Saved');
      setTimeout(() => setDetailsSaved(''), 1800);
    } catch {
      fetchProfile();   // reload rather than leave a value the server rejected
    }
  };

  const togglePreference = async (key: string, on: boolean, existing: any) => {
    try {
      if (on) {
        await profileAPI.addPreference({ preference_type: key, is_hard_constraint: false });
      } else if (existing) {
        await profileAPI.removePreference(existing.id);
      }
      fetchProfile();
    } catch {
      fetchProfile();
    }
  };

  // Re-posting the same preference updates its strictness rather than adding
  // a second row, so this needs no separate endpoint.
  const setStrict = async (key: string, strict: boolean) => {
    try {
      await profileAPI.addPreference({ preference_type: key, is_hard_constraint: strict });
      fetchProfile();
    } catch {
      fetchProfile();
    }
  };

  // Each toggle saves on its own — a settings panel with a separate Save
  // button silently loses changes when the user navigates away.
  const savePrivacy = async (patch: Record<string, boolean>) => {
    const optimistic = { ...(privacy || {}), ...patch };
    setPrivacy(optimistic);
    try {
      const res = await profileAPI.updatePrivacy(patch);
      setPrivacy(res.data);
    } catch {
      // Put the switch back rather than showing a state the server rejected.
      setPrivacy(privacy);
    }
  };

  // A newly added goal is validated in the background (the API answers before
  // the AI has judged it), so poll while anything is still pending.
  useEffect(() => {
    const pending = profile?.goals?.some((g: any) => g.profile_status === 'pending');
    if (!pending) return;
    const t = setInterval(fetchProfile, 3000);
    return () => clearInterval(t);
  }, [profile]);

  const fetchProfile = async () => {
    try {
      const response = await profileAPI.get();
      setProfile(response.data);
    } catch (error) {
      console.error('Failed to load profile', error);
    } finally {
      setLoading(false);
    }
  };

  const handleAddGoal = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newGoal) return;
    setGoalError('');
    try {
      await profileAPI.addGoal({ goal_type: newGoal });
      setNewGoal('');
      fetchProfile();
    } catch (error: any) {
      console.error('Failed to add goal', error);
      setGoalError(error?.response?.data?.detail || 'Failed to add goal.');
    }
  };

  const handleRemoveGoal = async (id: number) => {
    try {
      await profileAPI.removeGoal(id);
      fetchProfile();
    } catch (error) {
      console.error('Failed to remove goal', error);
    }
  };

  const handleAddAllergy = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newAllergy) return;
    setAllergyError('');
    try {
      await profileAPI.addAllergy({
        allergen: newAllergy,
        allergy_type: newAllergyType,
        // Severity only changes scoring for real allergies/intolerances; a
        // preference is a soft penalty regardless.
        severity: newAllergyType === 'preference' ? undefined : newAllergySeverity,
      });
      setNewAllergy('');
      fetchProfile();
    } catch (error: any) {
      console.error('Failed to add allergy', error);
      setAllergyError(error?.response?.data?.detail || 'Failed to add allergy.');
    }
  };

  const handleRemoveAllergy = async (id: number) => {
    try {
      await profileAPI.removeAllergy(id);
      fetchProfile();
    } catch (error) {
      console.error('Failed to remove allergy', error);
    }
  };

  if (loading) {
    return (
      <div className="profile-page loading-state">
        <div className="spinner"></div>
      </div>
    );
  }

  return (
    <div className="profile-page container">
      <div className="profile-header animate-fade-in-up">
        <h1>Health Profile</h1>
        <p>Personalize your intelligence reports by setting your goals and allergies.</p>
        {success && (
          <div className="success-banner">
            <CheckCircle2 size={18} /> {success}
          </div>
        )}
      </div>

      <div className="profile-grid">
        {/* Account Details */}
        <div className="profile-section card animate-fade-in-up stagger-1">
          <div className="section-header">
            <h2><Shield size={20} /> Account Details</h2>
          </div>
          <div className="account-details">
            <div className="detail-item">
              <span className="detail-label">Name</span>
              <span className="detail-value">{user?.full_name || '-'}</span>
            </div>
            <div className="detail-item">
              <span className="detail-label">Username</span>
              <span className="detail-value">{user?.username}</span>
            </div>
            <div className="detail-item">
              <span className="detail-label">Email</span>
              <span className="detail-value">{user?.email}</span>
            </div>
            <div className="detail-item">
              <span className="detail-label">Role</span>
              <span className="badge badge-primary">{user?.role}</span>
            </div>
          </div>
        </div>

        {/* Health Goals */}
        <div className="profile-section card animate-fade-in-up stagger-2">
          <div className="section-header">
            <h2><Target size={20} /> Health Goals</h2>
          </div>
          
          <form onSubmit={handleAddGoal} className="add-form">
            <input 
              type="text" 
              className="input" 
              placeholder="e.g. Weight Loss, Muscle Gain..."
              value={newGoal}
              onChange={(e) => setNewGoal(e.target.value)}
            />
            <button type="submit" className="btn btn-secondary">Add</button>
          </form>
          {goalError && (
            <div className="form-error"><AlertTriangle size={14} /> {goalError}</div>
          )}

          <div className="tags-list goals-list">
            {profile?.goals?.map((goal: any) => (
              <div key={goal.id} className={`goal-row goal-${goal.profile_status || 'ready'}`}>
                <div className="tag-item">
                  <span>{goal.goal_type}</span>
                  {goal.profile_status === 'pending' && (
                    <span className="goal-badge goal-badge-pending">
                      <Loader2 size={12} className="spin" /> checking
                    </span>
                  )}
                  {goal.profile_status === 'unsupported' && (
                    <span className="goal-badge goal-badge-unsupported">
                      <AlertTriangle size={12} /> not scored
                    </span>
                  )}
                  <button onClick={() => handleRemoveGoal(goal.id)} className="tag-remove">&times;</button>
                </div>
                {goal.status_message && goal.profile_status !== 'ready' && (
                  <p className="goal-status-message">{goal.status_message}</p>
                )}
              </div>
            ))}
            {(!profile?.goals || profile.goals.length === 0) && (
              <p className="empty-text">No goals set yet.</p>
            )}
          </div>
        </div>

        {/* Allergies */}
        <div className="profile-section card animate-fade-in-up stagger-3">
          <div className="section-header">
            <h2><AlertTriangle size={20} /> Allergies & Intolerances</h2>
          </div>
          
          <form onSubmit={handleAddAllergy} className="add-form allergy-form">
            <input
              type="text"
              className="input"
              list="known-allergens"
              placeholder="e.g. Peanuts, Milk, Gluten..."
              value={newAllergy}
              onChange={(e) => setNewAllergy(e.target.value)}
            />
            {/* Picking a canonical name gets full synonym coverage; free text
                is still allowed for allergens we don't have synonyms for. */}
            <datalist id="known-allergens">
              {knownAllergens.map((a) => <option key={a} value={a} />)}
            </datalist>
            <select
              className="input allergy-select"
              value={newAllergyType}
              onChange={(e) => setNewAllergyType(e.target.value)}
              aria-label="Type"
            >
              <option value="allergy">Allergy</option>
              <option value="intolerance">Intolerance</option>
              <option value="preference">Prefer to avoid</option>
            </select>
            {newAllergyType !== 'preference' && (
              <select
                className="input allergy-select"
                value={newAllergySeverity}
                onChange={(e) => setNewAllergySeverity(e.target.value)}
                aria-label="Severity"
              >
                <option value="severe">Severe</option>
                <option value="moderate">Moderate</option>
                <option value="mild">Mild</option>
              </select>
            )}
            <button type="submit" className="btn btn-secondary">Add</button>
          </form>
          {allergyError && (
            <div className="form-error"><AlertTriangle size={14} /> {allergyError}</div>
          )}
          <p className="allergy-hint">
            <strong>Prefer to avoid</strong> lowers a product's score but still lets it be
            recommended. <strong>Allergy</strong> and <strong>Intolerance</strong> rule it out.
          </p>

          <div className="tags-list">
            {profile?.allergies?.map((allergy: any) => (
              <div
                key={allergy.id}
                className={`tag-item ${allergy.allergy_type === 'preference' ? '' : 'tag-warning'}`}
              >
                <span>{allergy.allergen}</span>
                <span className="allergy-meta">
                  {allergy.allergy_type === 'preference'
                    ? 'avoid'
                    : `${allergy.allergy_type}${allergy.severity ? ` · ${allergy.severity}` : ''}`}
                </span>
                <button onClick={() => handleRemoveAllergy(allergy.id)} className="tag-remove">&times;</button>
              </div>
            ))}
            {(!profile?.allergies || profile.allergies.length === 0) && (
              <p className="empty-text">No allergies declared.</p>
            )}
          </div>
        </div>
        
        {/* About You — the attributes that drive community relevance */}
        <div className="profile-section card animate-fade-in-up stagger-3">
          <div className="section-header">
            <h2><UserCircle size={20} /> About You</h2>
            {detailsSaved && <span className="detail-saved"><CheckCircle2 size={13} /> {detailsSaved}</span>}
          </div>
          <p className="privacy-intro">
            Optional, and private by default. These are what let us match you to people
            with a comparable profile — without them, community experiences can only be
            compared on your goals and allergies.
          </p>

          <div className="detail-fields">
            {([
              ['age_range', 'Age range', options?.age_ranges],
              ['dietary_pattern', 'Dietary pattern', options?.dietary_patterns],
              ['activity_level', 'Activity level', options?.activity_levels],
            ] as const).map(([key, label, opts]) => (
              <div key={key} className="detail-field">
                <label htmlFor={`fld-${key}`}>{label}</label>
                <select
                  id={`fld-${key}`}
                  className="input"
                  value={profile?.profile?.[key] || ''}
                  disabled={!opts}
                  onChange={(e) => saveDetail({ [key]: e.target.value })}
                >
                  <option value="">Not specified</option>
                  {(opts || []).map((o) => (
                    <option key={o.value} value={o.value}>{o.label}</option>
                  ))}
                </select>
              </div>
            ))}
          </div>
        </div>

        {/* Anonymous context sharing — consent for community matching */}
        <div className="profile-section card animate-fade-in-up stagger-4">
          <div className="section-header">
            <h2><Lock size={20} /> Community Privacy</h2>
          </div>
          <p className="privacy-intro">
            Your profile is private. You can optionally let selected attributes be shared
            <strong> anonymously </strong> with experiences you post, so the platform can
            show your review to people with a comparable profile — and show you theirs.
            Your identity is never attached, and health details are never shared.
          </p>

          <label className="privacy-master">
            <input
              type="checkbox"
              checked={!!privacy?.allow_anonymous_context_sharing}
              onChange={(e) => savePrivacy({ allow_anonymous_context_sharing: e.target.checked })}
            />
            <span>Allow anonymous context sharing</span>
          </label>

          {/* Nothing below the master switch applies while it is off, so the
              controls are disabled rather than implying they still do. */}
          <div className={`privacy-options ${privacy?.allow_anonymous_context_sharing ? '' : 'disabled'}`}>
            {([
              ['share_age_range', 'Age range'],
              ['share_dietary_pattern', 'Dietary pattern'],
              ['share_activity_level', 'Activity level'],
              ['share_goals', 'Health goals'],
              ['share_allergies', 'Allergies'],
            ] as const).map(([key, label]) => (
              <label key={key} className="privacy-option">
                <input
                  type="checkbox"
                  disabled={!privacy?.allow_anonymous_context_sharing}
                  checked={!!privacy?.[key]}
                  onChange={(e) => savePrivacy({ [key]: e.target.checked })}
                />
                <span>{label}</span>
              </label>
            ))}
          </div>
        </div>

        {/* Nutrient preferences — soft constraints */}
        <div className="profile-section card animate-fade-in-up stagger-5">
          <div className="section-header">
            <h2><HeartPulse size={20} /> Dietary Preferences</h2>
          </div>
          <p className="privacy-intro">
            Individual nutrients to watch. These <strong>nudge</strong> a product's score
            rather than ruling it out — unlike an allergy or a dietary pattern. Mark one
            <strong> strict</strong> to make it a hard requirement instead.
          </p>

          <div className="pref-list">
            {(options?.preferences || []).map((opt) => {
              const active = (profile?.preferences || []).find(
                (p: any) => p.preference_type === opt.value);
              return (
                <div key={opt.value} className={`pref-row ${active ? 'active' : ''}`}>
                  <label className="pref-main">
                    <input
                      type="checkbox"
                      checked={!!active}
                      onChange={(e) => togglePreference(opt.value, e.target.checked, active)}
                    />
                    <span>{opt.label}</span>
                  </label>
                  {/* Only meaningful once the preference is on. */}
                  {active && (
                    <label className="pref-strict">
                      <input
                        type="checkbox"
                        checked={!!active.is_hard_constraint}
                        onChange={(e) => setStrict(opt.value, e.target.checked)}
                      />
                      <span>Strict</span>
                    </label>
                  )}
                </div>
              );
            })}
            {!options?.preferences?.length && (
              <p className="empty-text">Preferences unavailable right now.</p>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
