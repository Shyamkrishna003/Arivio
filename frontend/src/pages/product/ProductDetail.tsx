import { useState, useEffect } from 'react';
import { useParams, Link } from 'react-router-dom';
import { useSelector } from 'react-redux';
import type { RootState } from '../../store/index.ts';
import { productsAPI, personalizationAPI, aiAPI, profileAPI } from '../../services/api';
import { 
  ArrowLeft, CheckCircle2, AlertTriangle, Shield, 
  Leaf, BarChart3, Target, XCircle, Info, TrendingUp, TrendingDown, Minus,
  Brain, Star, Sparkles, MessageSquare, ChevronDown, ChevronUp, Lightbulb
} from 'lucide-react';
import './ProductDetail.css';

interface SuitabilityData {
  overall_score: number;
  verdict: string;
  confidence: number;
  allergen_safe: boolean;
  flags: Array<{
    flag_type: string;
    category: string;
    title: string;
    description: string;
    impact: number;
  }>;
  goal_alignments: Array<{
    goal: string;
    alignment: string;
    // null when the goal could not be scored
    score: number | null;
    reason: string;
    evaluated?: boolean;
  }>;
  nutritional_quality_score: number;
  ingredient_profile_score: number;
  breakdown: {
    // null when no goal could be evaluated — the goal term is dropped from the
    // composite and the remaining weights are renormalized.
    goal_alignment: number | null;
    nutritional_quality: number;
    ingredient_profile: number;
    allergen_conflict?: 'none' | 'preference' | 'trace' | 'confirmed';
    unscored_goals?: string[];
    weights?: Record<string, number>;
  };
}

interface AIReportData {
  summary: string;
  detailed_analysis: string;
  key_insights: string[];
  recommendations: string[];
  confidence_note: string;
  provider: string;
  model: string;
}

interface AlternativeProduct {
  product: any;
  suitability_score: number;
  verdict: string;
  match_reasons: string[];
}

export default function ProductDetail() {
  const { id } = useParams();
  const { isAuthenticated } = useSelector((state: RootState) => state.auth);
  const [product, setProduct] = useState<any>(null);
  const [suitability, setSuitability] = useState<SuitabilityData | null>(null);
  const [aiReport, setAiReport] = useState<AIReportData | null>(null);
  const [loading, setLoading] = useState(true);
  // Starts true when logged in: effects run after paint, so a `false` default
  // painted the "log in" empty state for a frame before the fetch even began.
  const [suitabilityLoading, setSuitabilityLoading] = useState(isAuthenticated);
  const [suitabilityError, setSuitabilityError] = useState(false);
  const [reportLoading, setReportLoading] = useState(false);
  const [reportExpanded, setReportExpanded] = useState(true);
  const [error, setError] = useState('');
  
  // Feedback state
  const [feedbackRating, setFeedbackRating] = useState(0);
  const [feedbackHover, setFeedbackHover] = useState(0);
  const [feedbackComment, setFeedbackComment] = useState('');
  const [feedbackSubmitted, setFeedbackSubmitted] = useState(false);
  const [feedbackLoading, setFeedbackLoading] = useState(false);
  const [showFeedbackForm, setShowFeedbackForm] = useState(false);

  // Alternatives state
  const [alternatives, setAlternatives] = useState<AlternativeProduct[]>([]);
  const [alternativesLoading, setAlternativesLoading] = useState(false);
  const [alternativesFetched, setAlternativesFetched] = useState(false);

  useEffect(() => {
    const fetchProduct = async () => {
      try {
        const response = await productsAPI.getById(Number(id));
        setProduct(response.data);

        // Record history if authenticated (we do it here after we know product exists,
        // but we actually need to wait for isAuthenticated to be true, so we can do it in another effect)
      } catch {
        setError('Failed to load product details.');
      } finally {
        setLoading(false);
      }
    };

    if (!id) return;
    // A non-numeric id can only come from a bad link — requesting
    // /products/NaN just returns a 422 we would report as a load failure.
    if (!Number.isFinite(Number(id))) {
      setError('That product link is not valid.');
      setLoading(false);
      setSuitabilityLoading(false);
      return;
    }
    fetchProduct();
  }, [id]);

  // Record scan history
  useEffect(() => {
    if (product && isAuthenticated) {
      profileAPI.recordHistory(product.id).catch(err => console.warn('Failed to record history', err));
    }
  }, [product, isAuthenticated]);

  // Fetch suitability after product is loaded and user is authenticated
  useEffect(() => {
    const fetchSuitability = async () => {
      if (!isAuthenticated) {
        setSuitabilityLoading(false);
        return;
      }
      if (!product) return;
      setSuitabilityLoading(true);
      setSuitabilityError(false);
      try {
        const response = await personalizationAPI.getSuitability(product.id);
        setSuitability(response.data);
      } catch {
        // Distinct from "not logged in" — telling a signed-in user to log in
        // because the request failed is misleading.
        setSuitabilityError(true);
      } finally {
        setSuitabilityLoading(false);
      }
    };

    fetchSuitability();
  }, [product, isAuthenticated]);

  // Fetch AI report after suitability is loaded
  useEffect(() => {
    const fetchReport = async () => {
      if (!product || !isAuthenticated || !suitability) return;
      setReportLoading(true);
      try {
        const response = await aiAPI.getReport(product.id);
        setAiReport(response.data);
      } catch {
        console.warn('AI report not available');
      } finally {
        setReportLoading(false);
      }
    };

    fetchReport();
  }, [product, isAuthenticated, suitability]);

  // Check existing feedback
  useEffect(() => {
    const fetchFeedback = async () => {
      if (!product || !isAuthenticated) return;
      try {
        const response = await aiAPI.getFeedback(product.id);
        if (response.data.feedback) {
          setFeedbackRating(response.data.feedback.rating);
          setFeedbackSubmitted(true);
        }
      } catch {
        // No feedback yet — that's fine
      }
    };

    fetchFeedback();
  }, [product, isAuthenticated]);

  // Fetch alternatives if suitability score is low
  useEffect(() => {
    const fetchAlternatives = async () => {
      // Only fetch if authenticated, score exists, and score is < 85
      if (!product || !isAuthenticated || !suitability || alternativesFetched) return;
      if (suitability.overall_score >= 85) return;
      
      setAlternativesLoading(true);
      try {
        const response = await personalizationAPI.getAlternatives(product.id, 3);
        setAlternatives(response.data.alternatives || []);
        setAlternativesFetched(true);
      } catch (err) {
        console.warn('Failed to fetch alternatives', err);
      } finally {
        setAlternativesLoading(false);
      }
    };

    fetchAlternatives();
  }, [product, isAuthenticated, suitability, alternativesFetched]);

  const handleFeedbackSubmit = async () => {
    if (!product || feedbackRating === 0) return;
    setFeedbackLoading(true);
    try {
      await aiAPI.submitFeedback({
        product_id: product.id,
        rating: feedbackRating,
        feedback_type: feedbackRating >= 4 ? 'helpful' : feedbackRating <= 2 ? 'inaccurate' : 'general',
        comment: feedbackComment || undefined,
      });
      setFeedbackSubmitted(true);
      setShowFeedbackForm(false);
    } catch {
      console.warn('Failed to submit feedback');
    } finally {
      setFeedbackLoading(false);
    }
  };

  // Simple markdown renderer for AI report
  const renderMarkdown = (text: string) => {
    if (!text) return null;
    
    const lines = text.split('\n');
    const elements: React.ReactNode[] = [];
    let listItems: string[] = [];
    
    const flushList = () => {
      if (listItems.length > 0) {
        elements.push(
          <ul key={`ul-${elements.length}`} className="report-list">
            {listItems.map((item, i) => (
              <li key={i} dangerouslySetInnerHTML={{ __html: formatInlineMarkdown(item) }} />
            ))}
          </ul>
        );
        listItems = [];
      }
    };

    const formatInlineMarkdown = (line: string): string => {
      // Bold
      line = line.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>');
      // Italic
      line = line.replace(/\*(.*?)\*/g, '<em>$1</em>');
      return line;
    };

    for (let i = 0; i < lines.length; i++) {
      const line = lines[i].trim();
      
      if (line.startsWith('## ')) {
        flushList();
        elements.push(<h3 key={`h3-${i}`} className="report-heading" dangerouslySetInnerHTML={{ __html: formatInlineMarkdown(line.replace('## ', '')) }} />);
      } else if (line.startsWith('### ')) {
        flushList();
        elements.push(<h4 key={`h4-${i}`} className="report-subheading" dangerouslySetInnerHTML={{ __html: formatInlineMarkdown(line.replace('### ', '')) }} />);
      } else if (line.startsWith('- ') || line.startsWith('* ')) {
        listItems.push(line.substring(2));
      } else if (line === '') {
        flushList();
      } else {
        flushList();
        elements.push(<p key={`p-${i}`} className="report-paragraph" dangerouslySetInnerHTML={{ __html: formatInlineMarkdown(line) }} />);
      }
    }
    flushList();
    
    return <div className="report-markdown">{elements}</div>;
  };

  if (loading) {
    return (
      <div className="product-page loading-state">
        <div className="spinner"></div>
        <p>Loading intelligence report...</p>
      </div>
    );
  }

  if (error || !product) {
    return (
      <div className="product-page error-state container">
        <AlertTriangle size={48} className="error-icon" />
        <h2>{error || 'Product not found'}</h2>
        <Link to="/dashboard" className="btn btn-primary mt-4">Return to Dashboard</Link>
      </div>
    );
  }

  const score = suitability?.overall_score ?? null;

  const getScoreColor = (s: number) => {
    if (s >= 70) return 'var(--color-accent-success)';
    if (s >= 40) return 'var(--color-accent-warning)';
    return 'var(--color-accent-danger)';
  };

  const getAlignmentIcon = (alignment: string) => {
    switch (alignment) {
      case 'excellent':
      case 'good':
        return <TrendingUp size={16} />;
      case 'poor':
      case 'bad':
        return <TrendingDown size={16} />;
      case 'not_evaluated':
        return <AlertTriangle size={16} />;
      default:
        return <Minus size={16} />;
    }
  };

  const getAlignmentClass = (alignment: string) => {
    switch (alignment) {
      case 'excellent': return 'alignment-excellent';
      case 'good': return 'alignment-good';
      case 'neutral': return 'alignment-neutral';
      case 'poor': return 'alignment-poor';
      case 'bad': return 'alignment-bad';
      case 'not_evaluated': return 'alignment-not-evaluated';
      default: return 'alignment-neutral';
    }
  };

  const getAlignmentLabel = (alignment: string) =>
    alignment === 'not_evaluated' ? 'not scored' : alignment;

  const getFlagIcon = (flagType: string) => {
    switch (flagType) {
      case 'danger': return <XCircle size={16} />;
      case 'warning': return <AlertTriangle size={16} />;
      case 'positive': return <CheckCircle2 size={16} />;
      default: return <Info size={16} />;
    }
  };

  return (
    <div className="product-page container">
      <div className="product-nav">
        <Link to="/dashboard" className="back-link">
          <ArrowLeft size={16} /> Back to Dashboard
        </Link>
      </div>

      <div className="product-header animate-fade-in-up">
        <div className="product-image-container">
          {product.image_url ? (
            <img src={product.image_url} alt={product.name} className="product-image" />
          ) : (
            <div className="product-image-placeholder">
              <Leaf size={48} />
            </div>
          )}
        </div>

        <div className="product-title-area">
          {product.brand && <span className="product-brand">{product.brand}</span>}
          <h1 className="product-title">{product.name}</h1>
          <div className="product-meta">
            {product.category && <span className="badge badge-primary">{product.category}</span>}
            <span className="badge badge-secondary">{product.serving_size || 'Serving size unknown'}</span>
          </div>
        </div>

        {/* Suitability Score Card */}
        <div className="product-score-card card">
          <div className="score-header">
            <h3>Personal Suitability</h3>
            {suitability && (
              <div className="confidence-badge">
                <Shield size={14} /> {suitability.confidence}% Confidence
              </div>
            )}
          </div>
          
          {suitabilityLoading ? (
            <div className="score-loading">
              <div className="spinner-sm"></div>
              <p>Analyzing for your profile...</p>
            </div>
          ) : score !== null && suitability ? (
            <div className="score-display">
              <div className="score-ring-large">
                <svg viewBox="0 0 100 100">
                  <circle cx="50" cy="50" r="45" className="ring-bg" />
                  <circle 
                    cx="50" cy="50" r="45" 
                    className="ring-progress" 
                    style={{ stroke: getScoreColor(score) }}
                    strokeDasharray={`${score * 2.83} 283`} 
                  />
                </svg>
                <div className="score-value" style={{ color: getScoreColor(score) }}>{score}</div>
              </div>
              
              <div className="score-text">
                <h4>{suitability.verdict}</h4>
                {!suitability.allergen_safe && (
                  <div className="allergen-warning-banner">
                    <XCircle size={16} /> Allergen conflict detected
                  </div>
                )}
              </div>
            </div>
          ) : suitabilityError ? (
            <div className="score-empty">
              <AlertTriangle size={24} />
              <p>We couldn't load your personalized score for this product.</p>
              <button
                className="btn btn-secondary btn-sm"
                onClick={() => product && setProduct({ ...product })}
              >
                Retry
              </button>
            </div>
          ) : (
            <div className="score-empty">
              <Shield size={24} />
              <p>Log in and set up your health profile to see personalized scores.</p>
            </div>
          )}
        </div>
      </div>

      {/* AI Intelligence Report Section */}
      {isAuthenticated && (
        <div className="ai-report-section card animate-fade-in-up stagger-1">
          <div className="ai-report-header" onClick={() => setReportExpanded(!reportExpanded)}>
            <div className="ai-report-title">
              <div className="ai-icon-wrapper">
                <Brain size={20} />
                <Sparkles size={12} className="sparkle-accent" />
              </div>
              <div>
                <h2>AI Intelligence Report</h2>
                <span className="ai-provider-badge">
                  {aiReport ? `Powered by ${aiReport.provider === 'fallback' ? 'ARIVIO Engine' : aiReport.provider}` : 'Generating...'}
                </span>
              </div>
            </div>
            <button className="expand-toggle">
              {reportExpanded ? <ChevronUp size={20} /> : <ChevronDown size={20} />}
            </button>
          </div>

          {reportExpanded && (
            <div className="ai-report-content">
              {reportLoading ? (
                <div className="report-loading">
                  <div className="ai-loading-animation">
                    <div className="ai-pulse-ring"></div>
                    <Brain size={24} className="ai-brain-icon" />
                  </div>
                  <div className="loading-text">
                    <p>Analyzing product for your health profile...</p>
                    <span className="loading-subtext">Cross-referencing ingredients, nutrition, and your goals</span>
                  </div>
                </div>
              ) : aiReport ? (
                <>
                  {/* Summary */}
                  <div className="report-summary">
                    <p>{aiReport.summary}</p>
                  </div>

                  {/* Key Insights */}
                  {aiReport.key_insights.length > 0 && (
                    <div className="report-insights">
                      <div className="insights-header">
                        <Lightbulb size={16} />
                        <h3>Key Insights</h3>
                      </div>
                      <div className="insights-grid">
                        {aiReport.key_insights.map((insight, idx) => (
                          <div key={idx} className="insight-card">
                            <span className="insight-number">{idx + 1}</span>
                            <p>{insight}</p>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}

                  {/* Detailed Analysis */}
                  <div className="report-analysis">
                    {renderMarkdown(aiReport.detailed_analysis)}
                  </div>

                  {/* Recommendations */}
                  {aiReport.recommendations.length > 0 && (
                    <div className="report-recommendations">
                      <h3><Target size={16} /> Recommendations</h3>
                      <div className="recommendations-list">
                        {aiReport.recommendations.map((rec, idx) => (
                          <div key={idx} className="recommendation-item">
                            <CheckCircle2 size={14} className="rec-icon" />
                            <p>{rec}</p>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}

                  {/* Confidence Note */}
                  <div className="report-disclaimer">
                    <Info size={14} />
                    <p>{aiReport.confidence_note}</p>
                  </div>

                  {/* Feedback Section */}
                  <div className="report-feedback">
                    {feedbackSubmitted ? (
                      <div className="feedback-thanks">
                        <CheckCircle2 size={16} />
                        <span>Thank you for your feedback! ({feedbackRating}/5 stars)</span>
                      </div>
                    ) : (
                      <>
                        <div className="feedback-prompt" onClick={() => setShowFeedbackForm(!showFeedbackForm)}>
                          <MessageSquare size={16} />
                          <span>Was this report helpful? Rate it to improve future reports.</span>
                        </div>
                        {showFeedbackForm && (
                          <div className="feedback-form">
                            <div className="star-rating">
                              {[1, 2, 3, 4, 5].map((star) => (
                                <button
                                  key={star}
                                  className={`star-btn ${star <= (feedbackHover || feedbackRating) ? 'active' : ''}`}
                                  onMouseEnter={() => setFeedbackHover(star)}
                                  onMouseLeave={() => setFeedbackHover(0)}
                                  onClick={() => setFeedbackRating(star)}
                                >
                                  <Star size={20} fill={star <= (feedbackHover || feedbackRating) ? 'currentColor' : 'none'} />
                                </button>
                              ))}
                            </div>
                            <textarea
                              className="feedback-textarea"
                              placeholder="Optional: Tell us how we can improve this report..."
                              value={feedbackComment}
                              onChange={(e) => setFeedbackComment(e.target.value)}
                              rows={2}
                            />
                            <button 
                              className="btn btn-primary feedback-submit-btn"
                              onClick={handleFeedbackSubmit}
                              disabled={feedbackRating === 0 || feedbackLoading}
                            >
                              {feedbackLoading ? 'Submitting...' : 'Submit Feedback'}
                            </button>
                          </div>
                        )}
                      </>
                    )}
                  </div>
                </>
              ) : (
                <div className="report-empty">
                  <Brain size={24} />
                  <p>AI report could not be generated. Try refreshing the page.</p>
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {/* Highly Suitable Alternatives Section */}
      {alternativesFetched && alternatives.length > 0 && (
        <div className="alternatives-section card animate-fade-in-up stagger-1">
          <div className="section-header">
            <h2><Target size={20} /> Better Alternatives for You</h2>
            <p className="text-sm text-muted mt-1">Based on your goals and preferences, these products scored higher.</p>
          </div>
          
          <div className="alternatives-grid mt-4">
            {alternatives.map((alt, idx) => (
              <Link to={`/products/${alt.product.id}`} key={alt.product.id} className="alternative-card card-glass" style={{ animationDelay: `${idx * 100}ms` }}>
                <div className="alt-score-badge" style={{ backgroundColor: getScoreColor(alt.suitability_score) }}>
                  {alt.suitability_score}
                </div>
                <div className="alt-content">
                  <div className="alt-thumb">
                    {alt.product.image_url ? (
                      <img src={alt.product.image_url} alt={alt.product.name} />
                    ) : (
                      <Leaf size={24} className="text-muted" />
                    )}
                  </div>
                  <div className="alt-info">
                    {alt.product.brand && <span className="alt-brand">{alt.product.brand}</span>}
                    <h5 className="alt-name">{alt.product.name}</h5>
                    <div className="alt-reasons">
                      {alt.match_reasons.map((reason, rIdx) => (
                        <div key={rIdx} className="alt-reason-item">
                          <CheckCircle2 size={12} className="text-accent-success" />
                          <span>{reason}</span>
                        </div>
                      ))}
                    </div>
                  </div>
                </div>
              </Link>
            ))}
          </div>
        </div>
      )}

      <div className="product-grid mt-6">
        {/* Goal Alignment */}
        {suitability && suitability.goal_alignments.length > 0 && (
          <div className="product-section card animate-fade-in-up stagger-2">
            <div className="section-header">
              <h2><Target size={20} /> Goal Alignment</h2>
            </div>
            <div className="goal-alignments">
              {suitability.goal_alignments.map((ga, idx) => (
                <div key={idx} className={`goal-item ${getAlignmentClass(ga.alignment)}`}>
                  <div className="goal-top">
                    <div className="goal-icon">{getAlignmentIcon(ga.alignment)}</div>
                    <div className="goal-info">
                      <span className="goal-name">{ga.goal}</span>
                      <span className={`goal-alignment-badge ${getAlignmentClass(ga.alignment)}`}>
                        {getAlignmentLabel(ga.alignment)}
                      </span>
                    </div>
                    {/* No placeholder number for a goal we could not score — a
                        "50" here reads as a real verdict and contradicts the
                        "not scored" messaging. */}
                    <div className="goal-score">
                      {ga.score === null ? <span className="goal-score-na">—</span> : ga.score}
                    </div>
                  </div>
                  <p className="goal-reason">{ga.reason}</p>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Flags */}
        {suitability && suitability.flags.length > 0 && (
          <div className="product-section card animate-fade-in-up stagger-3">
            <div className="section-header">
              <h2><Info size={20} /> Analysis Flags</h2>
            </div>
            <div className="flags-list">
              {suitability.flags.map((flag, idx) => (
                <div key={idx} className={`flag-item flag-${flag.flag_type}`}>
                  <div className="flag-icon">{getFlagIcon(flag.flag_type)}</div>
                  <div className="flag-content">
                    <span className="flag-title">{flag.title}</span>
                    <p className="flag-desc">{flag.description}</p>
                  </div>
                  <span className={`flag-impact ${flag.impact >= 0 ? 'positive' : 'negative'}`}>
                    {flag.impact > 0 ? '+' : ''}{flag.impact}
                  </span>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Score Breakdown */}
        {suitability && (
          <div className="product-section card animate-fade-in-up stagger-4">
            <div className="section-header">
              <h2><BarChart3 size={20} /> Score Breakdown</h2>
            </div>
            <div className="breakdown-bars">
              {/* Weights are renormalized when the goal term is dropped, so show
                  each component's actual contribution rather than implying a
                  fixed 50/30/20 split. */}
              {([
                ['Goal Alignment', suitability.breakdown.goal_alignment, 'goal_alignment'],
                ['Nutritional Quality', suitability.breakdown.nutritional_quality, 'nutritional_quality'],
                ['Ingredient Profile', suitability.breakdown.ingredient_profile, 'ingredient_profile'],
              ] as Array<[string, number | null, string]>).map(([label, value, key]) => {
                const weight = suitability.breakdown.weights?.[key];
                const dropped = value === null || weight === 0;
                return (
                  <div className={`breakdown-item${dropped ? ' breakdown-dropped' : ''}`} key={key}>
                    <div className="breakdown-label">
                      <span>
                        {label}
                        {weight !== undefined && !dropped && (
                          <span className="breakdown-weight">{Math.round(weight * 100)}%</span>
                        )}
                      </span>
                      <span className="breakdown-value">
                        {dropped ? 'not applied' : `${value}/100`}
                      </span>
                    </div>
                    <div className="breakdown-bar">
                      <div className="breakdown-fill" style={{
                        width: dropped ? '0%' : `${value}%`,
                        background: dropped ? 'transparent' : getScoreColor(value as number)
                      }}></div>
                    </div>
                  </div>
                );
              })}
            </div>
            {suitability.breakdown.goal_alignment === null && (
              <p className="breakdown-note">
                <AlertTriangle size={13} />
                <span>
                  None of your goals could be scored for this product, so the goal
                  component was excluded and the remaining measures were reweighted.
                  {/* The reweighted components aren't what the number reflects
                      when an allergen conflict caps it. */}
                  {suitability.breakdown.allergen_conflict === 'confirmed' ||
                   suitability.breakdown.allergen_conflict === 'trace'
                    ? ' The final score is set by the allergen conflict, not by these measures.'
                    : ' This score reflects general nutritional quality only.'}
                </span>
              </p>
            )}
            {suitability.breakdown.goal_alignment !== null &&
              !!suitability.breakdown.unscored_goals?.length && (
              <p className="breakdown-note">
                <Info size={13} /> Not scored for: {suitability.breakdown.unscored_goals.join(', ')}.
                Your other goals were still applied.
              </p>
            )}
          </div>
        )}

        {/* Ingredients Analysis */}
        <div className="product-section card animate-fade-in-up stagger-5">
          <div className="section-header">
            <h2><Leaf size={20} /> Ingredients</h2>
          </div>
          
          {product.ingredients && product.ingredients.length > 0 ? (
            <div className="ingredients-list">
              {product.ingredients.map((ing: any) => (
                <div key={ing.id} className="ingredient-item">
                  <span className="ingredient-name">{ing.name}</span>
                  {ing.percentage && <span className="ingredient-pct">{ing.percentage}%</span>}
                </div>
              ))}
            </div>
          ) : (
            <p className="empty-text">Ingredients list not available.</p>
          )}
        </div>

        {/* Nutrition */}
        <div className="product-section card animate-fade-in-up stagger-6">
          <div className="section-header">
            <h2><BarChart3 size={20} /> Nutrition</h2>
            {/* Stated explicitly: these values are per 100g, not per serving,
                and the score thresholds are calibrated on that basis. */}
            <p className="text-sm text-muted mt-1">Per 100g</p>
          </div>

          {product.nutrition ? (
            <div className="nutrition-grid">
              <div className="nutrition-item">
                <span className="nutr-label">Energy</span>
                <span className="nutr-val">{product.nutrition.energy_kcal || '-'} kcal</span>
              </div>
              <div className="nutrition-item">
                <span className="nutr-label">Protein</span>
                <span className="nutr-val">{product.nutrition.protein_g || '-'} g</span>
              </div>
              <div className="nutrition-item">
                <span className="nutr-label">Carbs</span>
                <span className="nutr-val">{product.nutrition.total_carbohydrates_g || '-'} g</span>
              </div>
              <div className="nutrition-item">
                <span className="nutr-label">Sugars</span>
                <span className="nutr-val">{product.nutrition.total_sugars_g || '-'} g</span>
              </div>
              <div className="nutrition-item">
                <span className="nutr-label">Fat</span>
                <span className="nutr-val">{product.nutrition.total_fat_g || '-'} g</span>
              </div>
              <div className="nutrition-item">
                <span className="nutr-label">Sat. Fat</span>
                <span className="nutr-val">{product.nutrition.saturated_fat_g || '-'} g</span>
              </div>
              <div className="nutrition-item">
                <span className="nutr-label">Fiber</span>
                <span className="nutr-val">{product.nutrition.fiber_g || '-'} g</span>
              </div>
              <div className="nutrition-item">
                <span className="nutr-label">Sodium</span>
                <span className="nutr-val">{product.nutrition.sodium_mg || '-'} mg</span>
              </div>
            </div>
          ) : (
            <p className="empty-text">Nutrition facts not available.</p>
          )}
        </div>
        
        {/* Allergens */}
        <div className="product-section card animate-fade-in-up stagger-7">
          <div className="section-header">
            <h2><AlertTriangle size={20} /> Allergens</h2>
          </div>
          
          {product.allergens && product.allergens.length > 0 ? (
            <div className="allergens-list">
              {product.allergens.map((alg: any, idx: number) => (
                <div key={idx} className="allergen-tag badge-warning">
                  {alg.allergen} ({alg.certainty})
                </div>
              ))}
            </div>
          ) : (
            <div className="allergen-safe">
              <CheckCircle2 size={16} />
              <span>No allergens declared</span>
            </div>
          )}
        </div>

      </div>
    </div>
  );
}
