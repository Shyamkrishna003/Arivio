import { useState, useEffect, useRef, useCallback } from 'react';
import { Link, useSearchParams, useNavigate } from 'react-router-dom';
import { productsAPI, resolveImageUrl } from '../../services/api';
import { Search, Package, ArrowRight, Shield, Globe, Loader2, AlertTriangle } from 'lucide-react';
import './ProductSearch.css';

interface ProductMatch {
  id: number;
  name: string;
  brand?: string;
  category?: string;
  image_url?: string;
  data_quality: string;
  match_score: number;
}

interface Suggestion {
  id: number;
  name: string;
  brand?: string;
  category?: string;
  image_url?: string;
  match_score: number;
}

interface ExternalCandidate {
  source: string;
  external_id: string;
  name: string;
  brand?: string;
  category?: string;
  image_url?: string;
}

// A trigram score below this is a loose match — shown, but labelled, so a
// fuzzy hit is never presented as though we are sure it is the right product.
const CONFIDENT_MATCH = 0.65;

const matchLabel = (score: number) => `${Math.round(score * 100)}% match`;

export default function ProductSearch() {
  const [searchParams, setSearchParams] = useSearchParams();
  const initialQuery = searchParams.get('q') || '';
  const navigate = useNavigate();

  const [query, setQuery] = useState(initialQuery);
  const [results, setResults] = useState<ProductMatch[]>([]);
  const [external, setExternal] = useState<ExternalCandidate[]>([]);
  const [externalSearched, setExternalSearched] = useState(false);
  const [loading, setLoading] = useState(false);
  const [total, setTotal] = useState(0);
  const [error, setError] = useState('');

  const [suggestions, setSuggestions] = useState<Suggestion[]>([]);
  const [showSuggestions, setShowSuggestions] = useState(false);
  const [importingId, setImportingId] = useState<string | null>(null);

  const searchBarRef = useRef<HTMLDivElement>(null);

  const handleSearch = useCallback(async (searchQuery: string) => {
    if (!searchQuery.trim()) {
      setResults([]);
      setExternal([]);
      setTotal(0);
      return;
    }

    setLoading(true);
    setError('');
    try {
      const response = await productsAPI.search(searchQuery);
      setResults(response.data.products);
      setTotal(response.data.total);
      setExternal(response.data.external_candidates || []);
      setExternalSearched(response.data.external_searched || false);
    } catch (err) {
      console.error('Search failed', err);
      setError('Search failed. Please try again.');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (initialQuery) {
      setQuery(initialQuery);
      handleSearch(initialQuery);
    }
  }, [initialQuery, handleSearch]);

  // ── Typeahead ──
  // Debounced so we issue one request per pause rather than one per keystroke,
  // and aborted on the next keystroke so a slow earlier response cannot land
  // after a newer one and overwrite the dropdown with stale rows.
  useEffect(() => {
    const trimmed = query.trim();
    // Whether these suggestions should be *shown* is derived below rather
    // than cleared here, so a stale list can never flash before the next
    // fetch resolves.
    if (trimmed.length < 2 || trimmed === initialQuery) return;

    const controller = new AbortController();
    const timer = setTimeout(async () => {
      try {
        const response = await productsAPI.suggest(trimmed, controller.signal);
        setSuggestions(response.data);
        setShowSuggestions(true);
      } catch {
        // An abort is the expected outcome for every keystroke but the last.
      }
    }, 250);

    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [query, initialQuery]);

  // Close the dropdown on an outside click, so it does not sit over results.
  useEffect(() => {
    const onClickOutside = (e: MouseEvent) => {
      if (searchBarRef.current && !searchBarRef.current.contains(e.target as Node)) {
        setShowSuggestions(false);
      }
    };
    document.addEventListener('mousedown', onClickOutside);
    return () => document.removeEventListener('mousedown', onClickOutside);
  }, []);

  const onSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    setShowSuggestions(false);
    setSearchParams(query ? { q: query } : {});
  };

  // Importing is what turns an Open Food Facts candidate into a product, so
  // the navigation has to wait for it — until then there is no id to open.
  const handleImport = async (candidate: ExternalCandidate) => {
    setImportingId(candidate.external_id);
    setError('');
    try {
      const response = await productsAPI.importExternal({
        source: candidate.source,
        external_id: candidate.external_id,
      });
      navigate(`/products/${response.data.id}`);
    } catch (err: any) {
      setError(
        err.response?.data?.detail ||
        'Could not import that product. Please try again.'
      );
      setImportingId(null);
    }
  };

  const activeQuery = searchParams.get('q');
  const trimmedQuery = query.trim();
  // Derived, not stored: the dropdown is only meaningful while the box holds
  // something new that we actually have suggestions for.
  const suggestionsVisible =
    showSuggestions &&
    suggestions.length > 0 &&
    trimmedQuery.length >= 2 &&
    trimmedQuery !== activeQuery;

  return (
    <div className="search-page container">
      <div className="search-header animate-fade-in-up">
        <h1>Product Search</h1>
        <p>Find products to analyze their ingredients and personal suitability.</p>

        <div className="search-bar-wrapper" ref={searchBarRef}>
          <form onSubmit={onSubmit} className="search-bar-large">
            <Search size={24} className="search-icon" />
            <input
              type="text"
              className="search-input-large"
              placeholder="Search by name or brand — partial and misspelled names work"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onFocus={() => suggestions.length > 0 && setShowSuggestions(true)}
              autoComplete="off"
            />
            <button type="submit" className="btn btn-primary btn-lg" disabled={loading}>
              {loading ? 'Searching...' : 'Search'}
            </button>
          </form>

          {suggestionsVisible && (
            <ul className="suggestions-dropdown">
              {suggestions.map((s) => (
                <li key={s.id}>
                  <Link
                    to={`/products/${s.id}`}
                    className="suggestion-row"
                    onClick={() => setShowSuggestions(false)}
                  >
                    <div className="suggestion-thumb">
                      {s.image_url ? (
                        <img src={resolveImageUrl(s.image_url)} alt="" />
                      ) : (
                        <Package size={16} className="placeholder-icon" />
                      )}
                    </div>
                    <div className="suggestion-text">
                      <span className="suggestion-name">{s.name}</span>
                      {s.brand && <span className="suggestion-brand">{s.brand}</span>}
                    </div>
                    <span className="suggestion-score">{matchLabel(s.match_score)}</span>
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>

      {error && (
        <div className="search-error">
          <AlertTriangle size={16} /> {error}
        </div>
      )}

      <div className="search-results animate-fade-in-up stagger-1">
        {loading ? (
          <div className="loading-state">
            <div className="spinner"></div>
            <p>Searching database...</p>
          </div>
        ) : (
          <>
            {results.length > 0 && (
              <>
                <div className="results-meta">
                  Found {total} {total === 1 ? 'product' : 'products'} for "{activeQuery}"
                  {results[0] && results[0].match_score < CONFIDENT_MATCH && (
                    <span className="results-hint">
                      {' '}— these are close matches, not exact ones. Pick the one you meant.
                    </span>
                  )}
                </div>
                <div className="results-grid">
                  {results.map((product) => (
                    <Link to={`/products/${product.id}`} key={product.id} className="product-card card">
                      <div className="product-card-img">
                        {product.image_url ? (
                          <img src={resolveImageUrl(product.image_url)} alt={product.name} />
                        ) : (
                          <Package size={40} className="placeholder-icon" />
                        )}
                      </div>
                      <div className="product-card-content">
                        {product.brand && <span className="product-brand">{product.brand}</span>}
                        <h3 className="product-name">{product.name}</h3>
                        <div className="product-badges">
                          <span className="badge badge-primary">{product.category || 'Unknown'}</span>
                          <span className="badge badge-success"><Shield size={12} /> {product.data_quality}</span>
                          <span
                            className={`badge ${product.match_score >= CONFIDENT_MATCH ? 'badge-success' : 'badge-warning'}`}
                          >
                            {matchLabel(product.match_score)}
                          </span>
                        </div>
                      </div>
                      <div className="product-card-action">
                        <ArrowRight size={20} />
                      </div>
                    </Link>
                  ))}
                </div>
              </>
            )}

            {external.length > 0 && (
              <div className="external-results">
                <div className="external-header">
                  <Globe size={18} />
                  <div>
                    <h3>Also found on Open Food Facts</h3>
                    <p>
                      These aren't in our catalogue yet. Pick one and we'll import its
                      ingredients and nutrition, then analyze it for you.
                    </p>
                  </div>
                </div>
                <div className="results-grid">
                  {external.map((candidate) => {
                    const busy = importingId === candidate.external_id;
                    return (
                      <button
                        type="button"
                        key={candidate.external_id}
                        className="product-card card external-card"
                        onClick={() => handleImport(candidate)}
                        disabled={importingId !== null}
                      >
                        <div className="product-card-img">
                          {candidate.image_url ? (
                            <img src={resolveImageUrl(candidate.image_url)} alt={candidate.name} />
                          ) : (
                            <Package size={40} className="placeholder-icon" />
                          )}
                        </div>
                        <div className="product-card-content">
                          {candidate.brand && <span className="product-brand">{candidate.brand}</span>}
                          <h3 className="product-name">{candidate.name}</h3>
                          <div className="product-badges">
                            <span className="badge badge-primary">{candidate.category || 'Unknown'}</span>
                            <span className="badge badge-muted">Not imported</span>
                          </div>
                        </div>
                        <div className="product-card-action">
                          {busy ? <Loader2 size={20} className="spin" /> : <ArrowRight size={20} />}
                        </div>
                      </button>
                    );
                  })}
                </div>
              </div>
            )}

            {activeQuery && results.length === 0 && external.length === 0 && (
              <div className="empty-state card">
                <Search size={48} className="empty-icon" />
                <h3>No products found</h3>
                <p>
                  We couldn't find anything matching "{activeQuery}"
                  {externalSearched ? ', here or on Open Food Facts' : ''}.
                </p>
                <Link
                  to={`/products/submit?name=${encodeURIComponent(activeQuery)}`}
                  className="btn btn-primary mt-4"
                >
                  Submit this Product
                </Link>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
