import { useState, useEffect } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useSelector, useDispatch } from 'react-redux';
import type { RootState } from '../../store';
import { logout } from '../../store/slices/authSlice';
import { Menu, X, User, LogOut, ScanLine, Sun, Moon } from 'lucide-react';
import { useTheme } from '../../hooks/useTheme';
import './Navbar.css';

export default function Navbar() {
  const [isScrolled, setIsScrolled] = useState(false);
  const [isMobileMenuOpen, setIsMobileMenuOpen] = useState(false);
  const { isAuthenticated } = useSelector((state: RootState) => state.auth);
  const dispatch = useDispatch();
  const navigate = useNavigate();
  const { theme, toggleTheme } = useTheme();

  useEffect(() => {
    const handleScroll = () => setIsScrolled(window.scrollY > 10);
    window.addEventListener('scroll', handleScroll);
    return () => window.removeEventListener('scroll', handleScroll);
  }, []);

  const handleLogout = () => {
    dispatch(logout());
    navigate('/');
  };

  return (
    <nav className={`navbar ${isScrolled ? 'navbar-scrolled' : ''}`} id="main-navbar">
      <div className="navbar-inner container">
        <Link to="/" className="navbar-brand" id="nav-brand">
          <div className="brand-icon">
            <ScanLine size={20} />
          </div>
          <span className="brand-text">ARIVIO</span>
        </Link>

        <div className={`navbar-links ${isMobileMenuOpen ? 'open' : ''}`}>
          {isAuthenticated ? (
            <>
              <Link to="/dashboard" className="nav-link" onClick={() => setIsMobileMenuOpen(false)}>Dashboard</Link>
              <Link to="/scan" className="nav-link" onClick={() => setIsMobileMenuOpen(false)}>Scan</Link>
              <Link to="/products" className="nav-link" onClick={() => setIsMobileMenuOpen(false)}>Products</Link>
            </>
          ) : (
            <>
              <a href="#features" className="nav-link" onClick={() => setIsMobileMenuOpen(false)}>Features</a>
              <a href="#how-it-works" className="nav-link" onClick={() => setIsMobileMenuOpen(false)}>How It Works</a>
            </>
          )}

          <div className="nav-actions">
            <button 
              onClick={toggleTheme} 
              className="btn btn-ghost btn-icon" 
              title={`Switch to ${theme === 'light' ? 'dark' : 'light'} mode`}
            >
              {theme === 'light' ? <Moon size={18} /> : <Sun size={18} />}
            </button>
            
            {isAuthenticated ? (
              <>
                <Link to="/profile" className="btn btn-ghost btn-icon" title="Profile">
                  <User size={18} />
                </Link>
                <button onClick={handleLogout} className="btn btn-ghost btn-icon" title="Logout">
                  <LogOut size={18} />
                </button>
              </>
            ) : (
              <>
                <Link to="/login" className="btn btn-ghost" onClick={() => setIsMobileMenuOpen(false)}>Log In</Link>
                <Link to="/register" className="btn btn-primary" onClick={() => setIsMobileMenuOpen(false)}>Get Started</Link>
              </>
            )}
          </div>
        </div>

        <button
          className="navbar-mobile-toggle btn btn-ghost btn-icon"
          onClick={() => setIsMobileMenuOpen(!isMobileMenuOpen)}
          aria-label="Toggle menu"
        >
          {isMobileMenuOpen ? <X size={22} /> : <Menu size={22} />}
        </button>
      </div>
    </nav>
  );
}
