import { useState } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { productsAPI } from '../../services/api';
import { PackagePlus, ArrowLeft, AlertCircle, Info } from 'lucide-react';
import './ProductSubmit.css';

export default function ProductSubmit() {
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();

  // Both entry points prefill what they already know: the scanner passes the
  // barcode it read, the search page passes the term that found nothing.
  const [name, setName] = useState(searchParams.get('name') || '');
  const [barcode, setBarcode] = useState(searchParams.get('barcode') || '');
  const [brand, setBrand] = useState('');
  const [category, setCategory] = useState('');
  const [ingredientsText, setIngredientsText] = useState('');

  const [error, setError] = useState('');
  const [submitting, setSubmitting] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!name.trim()) return;

    setError('');
    setSubmitting(true);
    try {
      const response = await productsAPI.submit({
        name: name.trim(),
        brand: brand.trim() || undefined,
        barcode: barcode.trim() || undefined,
        category: category.trim() || undefined,
        ingredients_text: ingredientsText.trim() || undefined,
      });
      navigate(`/products/${response.data.id}`);
    } catch (err: any) {
      setError(err?.response?.data?.detail || 'Failed to submit this product. Please try again.');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="submit-page container">
      <Link to="/products" className="back-link">
        <ArrowLeft size={16} /> Back to search
      </Link>

      <div className="submit-header animate-fade-in-up">
        <h1><PackagePlus size={26} /> Submit a Product</h1>
        <p>
          We don't have this one yet. Add what's on the packaging and we'll analyze it
          against your health profile straight away.
        </p>
      </div>

      <form onSubmit={handleSubmit} className="submit-form card animate-fade-in-up stagger-1">
        {error && (
          <div className="submit-error">
            <AlertCircle size={16} /> <span>{error}</span>
          </div>
        )}

        <div className="input-group">
          <label htmlFor="product-name">Product name <span className="required">*</span></label>
          <input
            id="product-name"
            type="text"
            className="input"
            placeholder="e.g. Roasted Almond Butter"
            value={name}
            onChange={(e) => setName(e.target.value)}
            required
          />
        </div>

        <div className="form-row">
          <div className="input-group">
            <label htmlFor="product-brand">Brand</label>
            <input
              id="product-brand"
              type="text"
              className="input"
              placeholder="e.g. Nature's Best"
              value={brand}
              onChange={(e) => setBrand(e.target.value)}
            />
          </div>

          <div className="input-group">
            <label htmlFor="product-category">Category</label>
            <input
              id="product-category"
              type="text"
              className="input"
              placeholder="e.g. Spreads"
              value={category}
              onChange={(e) => setCategory(e.target.value)}
            />
          </div>
        </div>

        <div className="input-group">
          <label htmlFor="product-barcode">Barcode</label>
          <input
            id="product-barcode"
            type="text"
            className="input"
            placeholder="e.g. 8901234567890"
            value={barcode}
            onChange={(e) => setBarcode(e.target.value)}
          />
        </div>

        <div className="input-group">
          <label htmlFor="product-ingredients">Ingredients</label>
          <textarea
            id="product-ingredients"
            className="input submit-textarea"
            rows={4}
            placeholder="Copy the list from the label, separated by commas — e.g. Almonds, Cane sugar, Palm oil, Salt"
            value={ingredientsText}
            onChange={(e) => setIngredientsText(e.target.value)}
          />
          <p className="field-hint">
            <Info size={13} /> List them in the order printed on the pack — labels run from
            most to least, and that ordering feeds the ingredient quality score.
          </p>
        </div>

        <button
          type="submit"
          className="btn btn-primary w-full"
          disabled={submitting || !name.trim()}
        >
          {submitting ? 'Submitting...' : 'Submit Product'}
        </button>

        <p className="submit-disclaimer">
          User-submitted products are marked unverified until we can confirm them against a
          trusted source, so scores from them are best treated as an estimate.
        </p>
      </form>
    </div>
  );
}
