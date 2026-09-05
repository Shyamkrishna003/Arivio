import { useState, useEffect } from 'react';
import { Link } from 'react-router-dom';
import { profileAPI, productsAPI, resolveImageUrl } from '../../services/api';
import {
  ArrowLeft, ArrowRight, Bookmark, Package, Shield, AlertTriangle, Loader2,
} from 'lucide-react';
import './SavedProducts.css';

interface SavedProduct {
  id: number;
  name: string;
  brand?: string | null;
  category?: string | null;
  image_url?: string | null;
  data_quality: string;
  saved_at?: string | null;
}

const PAGE_SIZE = 24;

export default function SavedProducts() {
  const [items, setItems] = useState<SavedProduct[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState('');
  // Ids currently being removed, so each row disables only its own button.
  const [removing, setRemoving] = useState<number[]>([]);

  useEffect(() => {
    const fetchSaved = async () => {
      try {
        const response = await profileAPI.getSaved(PAGE_SIZE, 0);
        setItems(response.data.items);
        setTotal(response.data.total);
      } catch {
        setError('Could not load your saved products.');
      } finally {
        setLoading(false);
      }
    };
    fetchSaved();
  }, []);

  const loadMore = async () => {
    setLoadingMore(true);
    setError('');
    try {
      const response = await profileAPI.getSaved(PAGE_SIZE, items.length);
      setItems((prev) => [...prev, ...response.data.items]);
      setTotal(response.data.total);
    } catch {
      setError('Could not load more.');
    } finally {
      setLoadingMore(false);
    }
  };

  const remove = async (productId: number) => {
    setRemoving((prev) => [...prev, productId]);
    setError('');
    try {
      await productsAPI.unsave(productId);
      setItems((prev) => prev.filter((p) => p.id !== productId));
      setTotal((prev) => Math.max(0, prev - 1));
    } catch {
      setError('Could not remove that product.');
    } finally {
      setRemoving((prev) => prev.filter((id) => id !== productId));
    }
  };

  return (
    <div className="saved-page container">
      <div className="saved-nav">
        <Link to="/dashboard" className="back-link">
          <ArrowLeft size={16} /> Back to Dashboard
        </Link>
      </div>

      <div className="saved-header">
        <h1><Bookmark size={24} /> Saved Products</h1>
        <p>
          {loading
            ? 'Loading your list…'
            : `${total} ${total === 1 ? 'product' : 'products'} saved.`}
        </p>
      </div>

      {error && (
        <div className="saved-error">
          <AlertTriangle size={16} /> {error}
        </div>
      )}

      {loading ? (
        <div className="loading-state">
          <div className="spinner"></div>
          <p>Loading saved products...</p>
        </div>
      ) : items.length === 0 ? (
        <div className="empty-state card">
          <Bookmark size={32} className="placeholder-icon" />
          <h3>Nothing saved yet</h3>
          <p>
            Open a product and hit Save to keep it here — handy for the things
            you buy again.
          </p>
          <Link to="/products" className="btn btn-primary">Find a product</Link>
        </div>
      ) : (
        <>
          <div className="saved-grid">
            {items.map((product) => (
              <div className="saved-card card" key={product.id}>
                <Link to={`/products/${product.id}`} className="saved-card-link">
                  <div className="saved-card-img">
                    {product.image_url ? (
                      <img src={resolveImageUrl(product.image_url)} alt={product.name} />
                    ) : (
                      <Package size={40} className="placeholder-icon" />
                    )}
                  </div>
                  <div className="saved-card-content">
                    {product.brand && <span className="product-brand">{product.brand}</span>}
                    <h3 className="saved-card-name">{product.name}</h3>
                    <div className="saved-card-badges">
                      <span className="badge badge-primary">{product.category || 'Unknown'}</span>
                      <span className="badge badge-success">
                        <Shield size={12} /> {product.data_quality}
                      </span>
                    </div>
                  </div>
                  <ArrowRight size={20} className="saved-card-arrow" />
                </Link>
                <button
                  type="button"
                  className="btn btn-secondary btn-sm saved-remove"
                  onClick={() => remove(product.id)}
                  disabled={removing.includes(product.id)}
                >
                  {removing.includes(product.id)
                    ? <Loader2 size={14} className="spin" />
                    : 'Remove'}
                </button>
              </div>
            ))}
          </div>

          {items.length < total && (
            <div className="saved-more">
              <button className="btn btn-secondary" onClick={loadMore} disabled={loadingMore}>
                {loadingMore ? 'Loading…' : `Load more (${total - items.length} left)`}
              </button>
            </div>
          )}
        </>
      )}
    </div>
  );
}
