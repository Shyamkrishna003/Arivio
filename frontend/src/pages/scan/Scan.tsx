import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { Html5Qrcode } from 'html5-qrcode';
import { productsAPI, profileAPI } from '../../services/api';
import { ScanLine, Search, AlertCircle, Lightbulb, Keyboard, Sun, Target, Maximize, ArrowRight, Camera, ImagePlus, CameraOff, Package, Clock } from 'lucide-react';
import './Scan.css';

interface RecentActivityItem {
  id: number;
  product_id: number;
  product_name: string;
  product_brand?: string;
  product_image_url?: string;
  scanned_at: string;
}

export default function Scan() {
  const [manualBarcode, setManualBarcode] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const [isCameraActive, setIsCameraActive] = useState(false);
  const [qrCodeInstance, setQrCodeInstance] = useState<Html5Qrcode | null>(null);
  const [recentScans, setRecentScans] = useState<RecentActivityItem[]>([]);
  const navigate = useNavigate();

  useEffect(() => {
    const fetchRecentScans = async () => {
      try {
        const response = await profileAPI.getDashboard();
        setRecentScans(response.data.recent_activity || []);
      } catch (err) {
        console.warn('Could not fetch recent scans', err);
      }
    };
    fetchRecentScans();
  }, []);
  useEffect(() => {
    const instance = new Html5Qrcode("reader");
    setQrCodeInstance(instance);
    return () => {
      if (instance.isScanning) {
        instance.stop().catch(console.error);
      }
    };
  }, []);

  const startCamera = async () => {
    if (!qrCodeInstance) return;
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
      setError("Camera access denied or unavailable.");
    }
  };

  const handleFileUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    if (!e.target.files || e.target.files.length === 0 || !qrCodeInstance) return;
    const file = e.target.files[0];
    
    setLoading(true);
    try {
      const decodedText = await qrCodeInstance.scanFile(file, true);
      handleBarcodeSubmit(decodedText);
    } catch (err) {
      console.error("Failed to scan file", err);
      setError("No barcode found in this image.");
      setLoading(false);
    }
  };

  const handleBarcodeSubmit = async (barcode: string) => {
    if (!barcode) return;
    
    setError('');
    setLoading(true);

    try {
      const response = await productsAPI.scan(barcode);
      // Navigate to product detail page
      navigate(`/products/${response.data.id}`);
    } catch (err: any) {
      if (err.response?.status === 404) {
        // Product not found, go to submit page
        navigate(`/products/submit?barcode=${barcode}`);
      } else {
        setError(err.response?.data?.detail || 'Failed to scan product. Please try again.');
      }
    } finally {
      setLoading(false);
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
        <p>Point your camera at a product barcode or enter it manually to retrieve instant specifications, pricing, and inventory status.</p>
      </div>

      <div className="scan-content animate-fade-in-up stagger-1">
        <div className="scan-camera card-glass">
          <div id="reader" className="scanner-container" style={{ display: isCameraActive ? 'block' : 'none' }}></div>
          
          {!isCameraActive && !loading && (
            <div className="custom-scanner-overlay">
              <div className="overlay-icon">
                <CameraOff size={28} />
              </div>
              <h3 className="overlay-title">Camera Access Required</h3>
              <p className="overlay-desc">Allow camera permissions to instantly scan barcodes and QR codes.</p>
              
              <div className="overlay-actions">
                <button className="btn btn-primary overlay-btn" onClick={startCamera}>
                  <Camera size={18} /> Request Permissions
                </button>
                <label className="btn btn-secondary overlay-btn cursor-pointer">
                  <ImagePlus size={18} /> Upload Image
                  <input type="file" accept="image/*" className="hidden" style={{ display: 'none' }} onChange={handleFileUpload} />
                </label>
              </div>
            </div>
          )}

          {loading && (
            <div className="scan-loading-overlay">
              <div className="spinner"></div>
              <p>Analyzing product...</p>
            </div>
          )}
        </div>

        <div className="scan-sidebar flex flex-col gap-6">
          <div className="scan-manual card-glass">
          <h3 className="flex items-center gap-2"><Keyboard size={20} className="text-accent" /> Manual Entry</h3>
          
          <form onSubmit={handleManualSubmit} className="manual-form mt-4">
            {error && (
              <div className="scan-error">
                <AlertCircle size={16} />
                <span>{error}</span>
              </div>
            )}
            
            <div className="input-group">
              <label className="text-xs font-bold text-muted uppercase tracking-wider mb-1">PRODUCT CODE / SKU</label>
              <div className="input-with-icon right-icon-style">
                <input
                  type="text"
                  className="input filled-input"
                  placeholder="e.g. 8490219485"
                  value={manualBarcode}
                  onChange={(e) => setManualBarcode(e.target.value)}
                  disabled={loading}
                />
                <ScanLine size={18} className="input-icon-right" />
              </div>
            </div>
            
            <button 
              type="submit" 
              className="btn btn-primary w-full flex items-center justify-center gap-2 mt-2"
              disabled={loading || !manualBarcode}
            >
              {loading ? 'Searching...' : 'Search Database'}
              {!loading && <ArrowRight size={16} />}
            </button>
          </form>
        </div>

        <div className="scan-info-card card-glass">
          <h4><Lightbulb size={18} className="text-accent" /> Scanning Tips</h4>
          <div className="scan-tips mt-4">
            <div className="scan-tip">
              <Sun size={16} className="tip-icon" />
              <span>Ensure well-lit conditions to reduce glare on reflective packaging.</span>
            </div>
            <div className="scan-tip">
              <Target size={16} className="tip-icon" />
              <span>Keep the device steady and align the barcode within the central frame.</span>
            </div>
            <div className="scan-tip">
              <Maximize size={16} className="tip-icon" />
              <span>Maintain a distance of 4-6 inches for optimal focus.</span>
            </div>
          </div>
        </div>
        </div>
      </div>

      <div className="recent-scans-section mt-16 animate-fade-in-up stagger-2">
        <div className="flex justify-between items-end mb-6">
          <h3 className="text-xl font-bold">Recent Scans</h3>
        </div>
        
        <div className="recent-scans-grid">
          {recentScans.length > 0 ? (
            recentScans.slice(0, 3).map((scan) => (
              <div 
                key={scan.id} 
                className="recent-scan-card card-glass cursor-pointer hover:border-accent"
                onClick={() => navigate(`/products/${scan.product_id}`)}
              >
                <div className="scan-thumb bg-bg-primary flex items-center justify-center">
                  {scan.product_image_url ? (
                    <img src={scan.product_image_url} alt={scan.product_name} className="w-full h-full object-cover" />
                  ) : (
                    <Package size={24} className="text-muted" />
                  )}
                </div>
                <div className="scan-meta flex flex-col justify-center">
                  <h5 className="font-semibold text-md m-0 mb-1 line-clamp-1">{scan.product_name}</h5>
                  {scan.product_brand && <span className="sku text-xs text-secondary uppercase tracking-wider">{scan.product_brand}</span>}
                  <div className="text-xs text-muted mt-2 flex items-center gap-1">
                    <Clock size={12} /> {new Date(scan.scanned_at).toLocaleDateString()}
                  </div>
                </div>
              </div>
            ))
          ) : (
            <div className="text-secondary text-sm">No recent scans found.</div>
          )}
        </div>
      </div>
    </div>
  );
}
