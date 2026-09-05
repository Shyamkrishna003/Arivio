import { useEffect, useState } from 'react';
import type { FormEvent } from 'react';
import { useNavigate, Link } from 'react-router-dom';
import { useSelector, useDispatch } from 'react-redux';
import type { RootState } from '../../store';
import { setUser } from '../../store/slices/authSlice';
import { authAPI, profileAPI } from '../../services/api';
import {
  ScanLine, Search, TrendingUp, Package, Shield,
  ArrowRight, Sparkles, Activity, Clock, Image as ImageIcon
} from 'lucide-react';
import './Dashboard.css';

interface RecentActivityItem {
  id: number;
  product_id: number;
  product_name: string;
  product_brand?: string;
  product_image_url?: string;
  scanned_at: string;
}

interface DashboardStats {
  total_scans: number;
  saved_products: number;
  active_goals: number;
  recent_activity: RecentActivityItem[];
}

export default function Dashboard() {
  const { user } = useSelector((state: RootState) => state.auth);
  const dispatch = useDispatch();
  const navigate = useNavigate();
  const [searchQuery, setSearchQuery] = useState('');
  const [stats, setStats] = useState<DashboardStats | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!user) {
      authAPI.me().then(res => dispatch(setUser(res.data))).catch(() => {});
    }
  }, [user, dispatch]);

  useEffect(() => {
    const fetchStats = async () => {
      try {
        const response = await profileAPI.getDashboard();
        setStats(response.data);
      } catch (error) {
        console.error("Failed to load dashboard stats", error);
      } finally {
        setLoading(false);
      }
    };
    fetchStats();
  }, []);

  const handleSearch = (e?: FormEvent) => {
    if (e) e.preventDefault();
    if (searchQuery.trim()) {
      navigate(`/products?q=${encodeURIComponent(searchQuery.trim())}`);
    }
  };

  return (
    <div className="dashboard" id="dashboard-page">
      <div className="container dashboard-content">
        {/* Welcome Header */}
        <div className="dash-header animate-fade-in-up">
          <div className="dash-greeting">
            <h1>
              Welcome{user ? `, ${user.full_name || user.username}` : ''}
              <Sparkles size={28} className="greeting-icon" />
            </h1>
            <p>Scan a product to get your personalized intelligence report</p>
          </div>
        </div>

        {/* Search Bar */}
        <div className="dash-search-container animate-fade-in-up stagger-1">
          <form className="dash-search" onSubmit={handleSearch}>
            <Search size={20} className="search-icon" />
            <input
              type="text"
              className="search-input"
              placeholder="Search products by name, brand, or category..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              id="dashboard-search"
            />
            <button type="submit" className="btn btn-primary search-btn" id="dashboard-search-btn">
              Search
            </button>
          </form>
        </div>

        {/* Quick Actions */}
        <div className="dash-actions animate-fade-in-up stagger-2">
          <Link to="/scan" className="action-card" id="action-scan">
            <div className="action-icon">
              <ScanLine size={24} />
            </div>
            <div className="action-info">
              <h3>Scan Product</h3>
              <p>Scan barcode or ingredient label</p>
            </div>
            <ArrowRight size={18} className="action-arrow" />
          </Link>

          <Link to="/products" className="action-card" id="action-search">
            <div className="action-icon">
              <Search size={24} />
            </div>
            <div className="action-info">
              <h3>Search Products</h3>
              <p>Find and analyze any product</p>
            </div>
            <ArrowRight size={18} className="action-arrow" />
          </Link>

          <Link to="/profile" className="action-card" id="action-profile">
            <div className="action-icon">
              <Shield size={24} />
            </div>
            <div className="action-info">
              <h3>Setup Profile</h3>
              <p>Personalize your analysis</p>
            </div>
            <ArrowRight size={18} className="action-arrow" />
          </Link>
        </div>

        {/* Stats Overview */}
        <div className="dash-stats animate-fade-in-up stagger-3">
          <div className="stat-card">
            <div className="stat-card-icon">
              <ScanLine size={20} />
            </div>
            <div className="stat-card-info">
              <span className="stat-card-value">{loading ? '...' : (stats?.total_scans || 0)}</span>
              <span className="stat-card-label">Total Scans</span>
            </div>
          </div>

          {/* The only stat that leads anywhere: the others are counters, this
              one is the door to the list it counts. */}
          <Link to="/saved" className="stat-card stat-card--link">
            <div className="stat-card-icon">
              <Package size={20} />
            </div>
            <div className="stat-card-info">
              <span className="stat-card-value">{loading ? '...' : (stats?.saved_products || 0)}</span>
              <span className="stat-card-label">Saved Products</span>
            </div>
          </Link>

          <div className="stat-card">
            <div className="stat-card-icon">
              <Activity size={20} />
            </div>
            <div className="stat-card-info">
              <span className="stat-card-value">{loading ? '...' : (stats?.active_goals || 0)}</span>
              <span className="stat-card-label">Active Goals</span>
            </div>
          </div>

          <div className="stat-card">
            <div className="stat-card-icon">
              <TrendingUp size={20} />
            </div>
            <div className="stat-card-info">
              <span className="stat-card-value">{loading ? '...' : '100%'}</span>
              <span className="stat-card-label">Profile Health</span>
            </div>
          </div>
        </div>

        {/* Recent Activity */}
        <div className="dash-recent animate-fade-in-up stagger-4">
          <h2>Recent Activity</h2>
          {loading ? (
            <div className="empty-state card">
              <div className="spinner-sm"></div>
              <p className="mt-2">Loading activity...</p>
            </div>
          ) : stats && stats.recent_activity.length > 0 ? (
            <div className="recent-activity-list card">
              {stats.recent_activity.map((activity) => (
                <Link to={`/products/${activity.product_id}`} key={activity.id} className="activity-item">
                  <div className="activity-icon-container">
                    {activity.product_image_url ? (
                      <img src={activity.product_image_url} alt={activity.product_name} className="activity-thumb" />
                    ) : (
                      <div className="activity-icon-placeholder">
                        <ImageIcon size={20} />
                      </div>
                    )}
                  </div>
                  <div className="activity-details">
                    <h4>{activity.product_name}</h4>
                    {activity.product_brand && <span className="activity-brand">{activity.product_brand}</span>}
                  </div>
                  <div className="activity-time">
                    <Clock size={14} />
                    <span>{new Date(activity.scanned_at).toLocaleDateString()}</span>
                  </div>
                </Link>
              ))}
            </div>
          ) : (
            <div className="empty-state card">
              <div className="empty-icon">
                <ScanLine size={48} />
              </div>
              <h3>No products scanned yet</h3>
              <p>Scan your first product to see personalized intelligence here</p>
              <Link to="/scan" className="btn btn-primary" id="empty-scan-cta">
                <ScanLine size={18} />
                Scan Your First Product
              </Link>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
