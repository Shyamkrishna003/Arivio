import { useState } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { productsAPI, resolveImageUrl } from '../../services/api';
import { PackagePlus, ArrowLeft, AlertCircle, Info, ArrowRight, ImagePlus, Loader2, X } from 'lucide-react';
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
  // Near-identical products the server refused to duplicate. The user has to
  // resolve this before the submission can go through.
  const [duplicates, setDuplicates] = useState<Array<{
    id: number; name: string; brand?: string; match_score: number;
  }> | null>(null);
  const [submitting, setSubmitting] = useState(false);

  // The photo is stored as soon as it's picked, so it survives a 409 from the
  // duplicate guard and the user doesn't have to choose it twice.
  const [imageId, setImageId] = useState<string | null>(null);
  const [imagePreview, setImagePreview] = useState<string | null>(null);
  const [imageType, setImageType] = useState('front');
  const [uploading, setUploading] = useState(false);

  const handleImage = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = '';
    if (!file) return;

    setUploading(true);
    setError('');
    try {
      const { data } = await productsAPI.uploadImage(file);
      setImageId(data.image_id);
      setImagePreview(resolveImageUrl(data.image_url) || null);
    } catch (err: any) {
      const detail = err?.response?.data?.detail;
      setError(
        (typeof detail === 'string' ? detail : null) ||
        'Could not upload that image. Try a JPEG, PNG or WebP photo.'
      );
    } finally {
      setUploading(false);
    }
  };

  const clearImage = () => {
    setImageId(null);
    setImagePreview(null);
  };

  const submit = async (force: boolean) => {
    if (!name.trim()) return;

    setError('');
    setDuplicates(null);
    setSubmitting(true);
    try {
      const response = await productsAPI.submit({
        name: name.trim(),
        brand: brand.trim() || undefined,
        barcode: barcode.trim() || undefined,
        category: category.trim() || undefined,
        ingredients_text: ingredientsText.trim() || undefined,
        image_id: imageId,
        image_type: imageType,
        force,
      });
      navigate(`/products/${response.data.id}`);
    } catch (err: any) {
      const detail = err?.response?.data?.detail;
      // The duplicate guard answers 409 with an object, not a string. Passing
      // that straight to setError would hand React an object to render and
      // crash the page.
      if (err?.response?.status === 409 && detail?.matches) {
        setDuplicates(detail.matches);
        setError(detail.message);
      } else {
        setError(
          (typeof detail === 'string' ? detail : null) ||
          'Failed to submit this product. Please try again.'
        );
      }
    } finally {
      setSubmitting(false);
    }
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    submit(false);
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

        {duplicates && duplicates.length > 0 && (
          <div className="duplicate-block">
            <p className="duplicate-lead">
              Adding this again would split its reviews and scores across two
              entries. Open the existing one if it's the same product.
            </p>
            {duplicates.map((d) => (
              <Link to={`/products/${d.id}`} key={d.id} className="duplicate-row">
                <span className="duplicate-name">
                  {d.brand ? `${d.brand} — ` : ''}{d.name}
                </span>
                <span className="duplicate-score">
                  {Math.round(d.match_score * 100)}%
                </span>
                <ArrowRight size={15} />
              </Link>
            ))}
            <button type="button" className="btn btn-secondary"
              disabled={submitting} onClick={() => submit(true)}>
              {submitting ? 'Submitting…' : 'No — this is a different product, add it'}
            </button>
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

        <div className="input-group">
          <label>Photo</label>
          {imagePreview ? (
            <div className="image-preview">
              <img src={imagePreview} alt="The product photo you uploaded" />
              <button type="button" className="image-remove" onClick={clearImage}
                aria-label="Remove photo">
                <X size={15} />
              </button>
            </div>
          ) : (
            <label className="image-dropzone">
              <input type="file" accept="image/*" style={{ display: 'none' }}
                disabled={uploading} onChange={handleImage} />
              {uploading ? <Loader2 size={20} className="spin" /> : <ImagePlus size={20} />}
              <span>{uploading ? 'Uploading…' : 'Add a photo of the pack or its label'}</span>
            </label>
          )}

          {imagePreview && (
            <select className="input mt-2" value={imageType}
              onChange={(e) => setImageType(e.target.value)}>
              <option value="front">Front of pack</option>
              <option value="ingredients">Ingredients label</option>
              <option value="nutrition">Nutrition label</option>
            </select>
          )}

          <p className="field-hint">
            <Info size={13} /> Optional. Only a front-of-pack photo is used as the
            product's picture — a close-up of a label is kept with the product but
            makes a poor thumbnail. Photos are visible to other users, and location
            data is stripped before upload.
          </p>
        </div>

        <button
          type="submit"
          className="btn btn-primary w-full"
          disabled={submitting || uploading || !name.trim()}
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
