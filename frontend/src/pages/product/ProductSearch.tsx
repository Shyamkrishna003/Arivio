import { useState, useEffect } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { productsAPI } from '../../services/api';
import { Search, Package, ArrowRight, Shield } from 'lucide-react';
import './ProductSearch.css';

export default function ProductSearch() {
  const [searchParams, setSearchParams] = useSearchParams();
  const initialQuery = searchParams.get('q') || '';
  
  const [query, setQuery] = useState(initialQuery);
  const [results, setResults] = useState<any[]>([]);
  const [loading, setLoading] = useState(false);
  const [total, setTotal] = useState(0);

  const handleSearch = async (searchQuery: string) => {
    if (!searchQuery.trim()) {
      setResults([]);
      setTotal(0);
      return;
    }
    
    setLoading(true);
    try {
      const response = await productsAPI.search(searchQuery);
      setResults(response.data.products);
      setTotal(response.data.total);
    } catch (error) {
      console.error('Search failed', error);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (initialQuery) {
      handleSearch(initialQuery);
    }
  }, [initialQuery]);

  const onSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    setSearchParams(query ? { q: query } : {});
  };

  return (
    <div className="search-page container">
      <div className="search-header animate-fade-in-up">
        <h1>Product Search</h1>
        <p>Find products to analyze their ingredients and personal suitability.</p>
        
        <form onSubmit={onSubmit} className="search-bar-large">
          <Search size={24} className="search-icon" />
          <input
            type="text"
            className="search-input-large"
            placeholder="Search by name, brand, or category..."
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
          <button type="submit" className="btn btn-primary btn-lg" disabled={loading}>
            {loading ? 'Searching...' : 'Search'}
          </button>
        </form>
      </div>

      <div className="search-results animate-fade-in-up stagger-1">
        {loading ? (
          <div className="loading-state">
            <div className="spinner"></div>
            <p>Searching database...</p>
          </div>
        ) : results.length > 0 ? (
          <>
            <div className="results-meta">
              Found {total} {total === 1 ? 'product' : 'products'} for "{searchParams.get('q')}"
            </div>
            <div className="results-grid">
              {results.map((product) => (
                <Link to={`/products/${product.id}`} key={product.id} className="product-card card">
                  <div className="product-card-img">
                    {product.image_url ? (
                      <img src={product.image_url} alt={product.name} />
                    ) : (
                      <Package size={40} className="placeholder-icon" />
                    )}
                  </div>
                  <div className="product-card-content">
                    {product.brand && <span className="product-brand">{product.brand}</span>}
                    <h3 className="product-name">{product.name}</h3>
                    <div className="product-badges">
                      <span className="badge badge-primary">{product.category || 'Unknown'}</span>
                      <span className="badge badge-success"><Shield size={12}/> {product.data_quality}</span>
                    </div>
                  </div>
                  <div className="product-card-action">
                    <ArrowRight size={20} />
                  </div>
                </Link>
              ))}
            </div>
          </>
        ) : searchParams.get('q') ? (
          <div className="empty-state card">
            <Search size={48} className="empty-icon" />
            <h3>No products found</h3>
            <p>We couldn't find any products matching "{searchParams.get('q')}".</p>
            <Link to={`/products/submit?name=${encodeURIComponent(searchParams.get('q') || '')}`} className="btn btn-primary mt-4">
              Submit this Product
            </Link>
          </div>
        ) : null}
      </div>
    </div>
  );
}
