import { useState, useEffect, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { Html5Qrcode } from 'html5-qrcode';
import { ocrAPI, productsAPI } from '../../services/api';
import {
  ScanLine, ScanText, AlertCircle, Lightbulb, Keyboard, Sun, Target, Maximize,
  ArrowRight, Camera, ImagePlus, UploadCloud, ShieldCheck,
} from 'lucide-react';
import './Scan.css';

type Method = 'barcode' | 'label' | 'manual';

const METHODS: { id: Method; label: string; icon: typeof ScanLine; hint: string }[] = [
  { id: 'barcode', label: 'Barcode', icon: ScanLine, hint: 'Exact match, fastest' },
  { id: 'label', label: 'Label Photo', icon: ScanText, hint: 'When there’s no barcode' },
  { id: 'manual', label: 'Enter Code', icon: Keyboard, hint: 'Type the number' },
];

export default function Scan() {
  const [method, setMethod] = useState<Method>('barcode');
  const [manualBarcode, setManualBarcode] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  // What the loader says. Reading a label takes a few seconds and runs through
  // distinct stages, so a single frozen "Analyzing..." reads as a hang.
  const [status, setStatus] = useState('');
  // Thumbnail of the photo being processed, so it's obvious which image the
  // loader belongs to.
  const [preview, setPreview] = useState<string | null>(null);
  const [isCameraActive, setIsCameraActive] = useState(false);
  const [isDragging, setIsDragging] = useState(false);
  const [qrCodeInstance, setQrCodeInstance] = useState<Html5Qrcode | null>(null);
  const navigate = useNavigate();

  // Object URLs must be revoked or the blob leaks for the page's lifetime.
  const previewRef = useRef<string | null>(null);

  useEffect(() => {
    const instance = new Html5Qrcode("reader");
    setQrCodeInstance(instance);
    return () => {
      if (instance.isScanning) {
        instance.stop().catch(console.error);
      }
    };
  }, []);

  useEffect(() => () => {
    if (previewRef.current) URL.revokeObjectURL(previewRef.current);
  }, []);

  const stopCamera = async () => {
    if (qrCodeInstance?.isScanning) {
      try {
        await qrCodeInstance.stop();
      } catch (e) {
        console.error(e);
      }
    }
    setIsCameraActive(false);
  };

  // Leaving the barcode tab releases the camera. Holding it open behind a
  // hidden panel keeps the recording indicator lit for no reason.
  const selectMethod = async (next: Method) => {
    if (next === method) return;
    setError('');
    if (next !== 'barcode') await stopCamera();
    setMethod(next);
  };

  const startCamera = async () => {
    if (!qrCodeInstance) return;
    setError('');
    try {
      await qrCodeInstance.start(
        { facingMode: "environment" },
        { fps: 10, qrbox: { width: 250, height: 150 }, aspectRatio: 1.0 },
        (decodedText) => {
          if (qrCodeInstance.isScanning) {
            qrCodeInstance.stop().then(() => {
              setIsCameraActive(false);
              handleBarcodeSubmit(decodedText);
            }).catch(console.error);
          }
        },
        () => {} // ignore frame errors
      );
      setIsCameraActive(true);
    } catch (err) {
      console.error("Failed to start camera", err);
      setError("Camera access denied or unavailable. You can upload a photo instead.");
    }
  };

  const showPreview = (file: File) => {
    if (previewRef.current) URL.revokeObjectURL(previewRef.current);
    const url = URL.createObjectURL(file);
    previewRef.current = url;
    setPreview(url);
  };

  const clearPreview = () => {
    if (previewRef.current) URL.revokeObjectURL(previewRef.current);
    previewRef.current = null;
    setPreview(null);
  };

  // The identification ladder from PRD §13, cheapest and most reliable first:
  // decode a barcode locally, and only fall back to reading the label with a
  // vision model when there is no barcode to find. A decoded barcode is an
  // exact identity; a read label is a proposal the user still has to confirm.
  const processImage = async (file: File) => {
    if (!qrCodeInstance) return;

    setError('');
    showPreview(file);
    setLoading(true);
    setStatus('Checking for a barcode…');

    try {
      const decodedText = await qrCodeInstance.scanFile(file, true);
      setStatus('Barcode found — looking it up…');
      await handleBarcodeSubmit(decodedText);
      return;
    } catch {
      // No barcode in the image — expected for a photo of an ingredient panel.
    }

    setStatus('Reading the label…');
    try {
      const { data } = await ocrAPI.scanLabel(file);
      // A barcode legible in the photo and confirmed by its check digit
      // resolves to a real product — no confirmation step needed.
      if (data.barcode_product_id) {
        setStatus('Found it — opening…');
        navigate(`/products/${data.barcode_product_id}`);
        return;
      }
      setStatus('Almost there…');
      navigate(`/scan/confirm/${data.extraction_id}`);
    } catch (err: any) {
      setError(
        err.response?.data?.detail ||
        'We could not read that image. Try a sharper, well-lit photo of the label.'
      );
      setLoading(false);
      setStatus('');
      clearPreview();
    }
  };

  const handleFileUpload = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    // Reset the input so choosing the same file twice still fires a change.
    e.target.value = '';
    if (file) processImage(file);
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(false);
    const file = e.dataTransfer.files?.[0];
    if (!file) return;
    if (!file.type.startsWith('image/')) {
      setError('That file is not an image. Drop a JPEG, PNG or WebP photo.');
      return;
    }
    processImage(file);
  };

  const handleBarcodeSubmit = async (barcode: string) => {
    if (!barcode) return;

    setError('');
    setLoading(true);

    try {
      const response = await productsAPI.scan(barcode);
      navigate(`/products/${response.data.id}`);
    } catch (err: any) {
      if (err.response?.status === 404) {
        // Product not found, go to submit page
        navigate(`/products/submit?barcode=${barcode}`);
      } else {
        setError(err.response?.data?.detail || 'Failed to scan product. Please try again.');
        setLoading(false);
        setStatus('');
        clearPreview();
      }
    }
  };

  const handleManualSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    handleBarcodeSubmit(manualBarcode);
  };

  return (
    <div className="scan-page container">
      <div className="scan-header animate-fade-in-up">
        <h1>Scan Product</h1>
        <p>
          Scan a barcode for an exact match — or photograph the ingredient panel
          and we’ll read it. You check what we read before anything is analyzed.
        </p>
      </div>

      {/* One row, three equal choices. Previously the label reader was a
          secondary button inside the camera's empty state, which vanished the
          moment the camera turned on — exactly when someone staring at a pack
          with no barcode needs it. */}
      {/* aria-pressed toggles rather than role="tablist": a real tablist
          promises arrow-key navigation between tabs, and claiming that
          without implementing it is worse for a screen-reader user than not
          claiming it at all. */}
      <div className="method-picker animate-fade-in-up" aria-label="Scanning method">
        {METHODS.map(({ id, label, icon: Icon, hint }) => (
          <button
            key={id}
            type="button"
            aria-pressed={method === id}
            className={`method-tab ${method === id ? 'is-active' : ''}`}
            onClick={() => selectMethod(id)}
            disabled={loading}
          >
            <Icon size={20} />
            <span className="method-label">{label}</span>
            <span className="method-hint">{hint}</span>
          </button>
        ))}
      </div>

      {/* Page level, not inside one column: any of the three entry points can
          fail, and an upload error shown under the barcode box reads as a
          barcode problem. */}
      {error && (
        <div className="scan-error scan-error-page animate-fade-in-up">
          <AlertCircle size={16} />
          <span>{error}</span>
        </div>
      )}

      <div className="scan-stage animate-fade-in-up stagger-1">
        {/* Every panel stays mounted: #reader must exist in the DOM from first
            render, because Html5Qrcode binds to it by id on mount. */}
        <section className="scan-panel card-glass" hidden={method !== 'barcode'}>
          <div id="reader" className="scanner-container" style={{ display: isCameraActive ? 'block' : 'none' }}></div>

          {!isCameraActive && (
            <div className="panel-body">
              <div className="panel-icon"><ScanLine size={26} /></div>
              <h3 className="panel-title">Scan a barcode</h3>
              <p className="panel-desc">
                Point your camera at the barcode, or upload a photo of the pack.
              </p>
              <div className="panel-actions">
                <button className="btn btn-primary panel-btn" onClick={startCamera} disabled={loading}>
                  <Camera size={18} /> Use Camera
                </button>
                <label className="btn btn-secondary panel-btn cursor-pointer">
                  <ImagePlus size={18} /> Upload Photo
                  <input type="file" accept="image/*" style={{ display: 'none' }}
                    disabled={loading} onChange={handleFileUpload} />
                </label>
              </div>
              <p className="panel-note">
                An uploaded photo is checked for a barcode first, then read as a label.
              </p>
            </div>
          )}

          {isCameraActive && (
            <div className="camera-footer">
              <span className="live-dot" /> Camera active — hold the barcode in frame
              <button className="btn-link" type="button" onClick={stopCamera}>Stop</button>
            </div>
          )}
        </section>

        {/* dragover fires continuously while the pointer moves, so isDragging
            is only written on the transition into the dragging state. */}
        <section
          className={`scan-panel card-glass ${isDragging ? 'is-dragging' : ''}`}
          hidden={method !== 'label'}
          onDragOver={(e) => { e.preventDefault(); if (!isDragging) setIsDragging(true); }}
          onDragLeave={() => setIsDragging(false)}
          onDrop={handleDrop}
        >
          <label className="dropzone">
            {/* Deliberately no `capture` attribute: it makes some mobile
                browsers open the camera and offer no way to pick an existing
                photo, which contradicts what this panel says it does. */}
            <input type="file" accept="image/*"
              style={{ display: 'none' }} disabled={loading} onChange={handleFileUpload} />
            <div className="panel-icon"><UploadCloud size={26} /></div>
            <h3 className="panel-title">Photograph the label</h3>
            <p className="panel-desc">
              Take or drop a photo of the <strong>ingredient list</strong> or{' '}
              <strong>nutrition panel</strong>. Tap anywhere in this box to choose one.
            </p>
            <span className="btn btn-primary panel-btn">
              <ImagePlus size={18} /> Choose Photo
            </span>
            <p className="panel-note">
              <ShieldCheck size={13} /> You review and correct everything we read
              before it’s analyzed.
            </p>
          </label>
        </section>

        <section className="scan-panel card-glass" hidden={method !== 'manual'}>
          <div className="panel-body">
            <div className="panel-icon"><Keyboard size={26} /></div>
            <h3 className="panel-title">Enter the code</h3>
            <p className="panel-desc">Type the barcode number printed under the bars.</p>
            <form onSubmit={handleManualSubmit} className="manual-form">
              <div className="input-with-icon right-icon-style">
                <input
                  type="text"
                  className="input filled-input"
                  placeholder="e.g. 8901719103032"
                  inputMode="numeric"
                  value={manualBarcode}
                  onChange={(e) => setManualBarcode(e.target.value)}
                  disabled={loading}
                />
                <ScanLine size={18} className="input-icon-right" />
              </div>
              <button
                type="submit"
                className="btn btn-primary w-full flex items-center justify-center gap-2"
                disabled={loading || !manualBarcode}
              >
                {loading ? 'Searching…' : 'Search Database'}
                {!loading && <ArrowRight size={16} />}
              </button>
            </form>
          </div>
        </section>

        {/* Covers the whole stage, not one panel, so it appears where the user
            is looking regardless of which method they used. */}
        {loading && (
          <div className="scan-loading-overlay">
            {preview && <img src={preview} alt="" className="loading-preview" />}
            <div className="spinner"></div>
            <p className="loading-status">{status || 'Analyzing product…'}</p>
            <p className="loading-sub">This usually takes a few seconds.</p>
          </div>
        )}
      </div>

      <div className="scan-info-card card-glass animate-fade-in-up stagger-2">
        <h4><Lightbulb size={18} className="text-accent" /> Getting a good scan</h4>
        <div className="scan-tips">
          <div className="scan-tip">
            <Sun size={16} className="tip-icon" />
            <span>Good light, no glare — reflective packaging is the usual culprit.</span>
          </div>
          <div className="scan-tip">
            <Target size={16} className="tip-icon" />
            <span>Hold steady and keep the barcode inside the frame.</span>
          </div>
          <div className="scan-tip">
            <Maximize size={16} className="tip-icon" />
            <span>Stay 4–6 inches away so the camera can focus.</span>
          </div>
          <div className="scan-tip">
            <ScanText size={16} className="tip-icon" />
            <span>For labels, fill the frame with the panel and shoot square-on.</span>
          </div>
        </div>
      </div>

    </div>
  );
}
