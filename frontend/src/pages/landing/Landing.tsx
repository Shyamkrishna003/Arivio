import { Link } from 'react-router-dom';
import {
  ScanLine, Shield, Users, Brain, ChevronRight, Sparkles,
  Search, FlaskConical, HeartPulse, BarChart3, Eye, Zap,
  ArrowRight, CheckCircle2
} from 'lucide-react';
import './Landing.css';

export default function Landing() {
  return (
    <div className="landing" id="landing-page">
      {/* Hero Section */}
      <section className="hero" id="hero-section">
        <div className="hero-bg">
        </div>

        <div className="container hero-content">
          <div className="hero-badge animate-fade-in">
            <Sparkles size={14} />
            <span>AI-Powered Product Intelligence</span>
          </div>

          <h1 className="hero-title animate-fade-in-up">
            <span>Look Beyond</span>
            <br />
            the Label
          </h1>

          <p className="hero-subtitle animate-fade-in-up stagger-1">
            Scan any product. Get a personalized intelligence report based on 
            scientific evidence, community experiences, and your unique health context.
          </p>

          <div className="hero-actions animate-fade-in-up stagger-2">
            <Link to="/register" className="btn btn-primary btn-lg" id="hero-cta-primary">
              <ScanLine size={20} />
              Start Scanning Free
            </Link>
            <a href="#how-it-works" className="btn btn-secondary btn-lg" id="hero-cta-secondary">
              How It Works
              <ChevronRight size={18} />
            </a>
          </div>

          <div className="hero-stats animate-fade-in-up stagger-3">
            <div className="stat">
              <span className="stat-value">1M+</span>
              <span className="stat-label">Products Analyzed</span>
            </div>
            <div className="stat-divider" />
            <div className="stat">
              <span className="stat-value">50K+</span>
              <span className="stat-label">Ingredients Known</span>
            </div>
            <div className="stat-divider" />
            <div className="stat">
              <span className="stat-value">100%</span>
              <span className="stat-label">Personalized</span>
            </div>
          </div>
        </div>
      </section>

      {/* Features Section */}
      <section className="features-section" id="features">
        <div className="container">
          <div className="section-header animate-fade-in-up">
            <span className="section-tag">Features</span>
            <h2>
              Not a Score. <span>An Understanding.</span>
            </h2>
            <p className="section-desc">
              A product doesn't have one universal meaning for every person. ARIVIO provides
              decision support — not judgement.
            </p>
          </div>

          <div className="features-grid">
            <div className="feature-card animate-fade-in-up stagger-1" id="feature-scan">
              <div className="feature-icon">
                <ScanLine size={28} />
              </div>
              <h3>Smart Product Scan</h3>
              <p>Scan barcodes, ingredient labels, or nutrition facts. Upload product images. Our AI identifies and analyzes instantly.</p>
            </div>

            <div className="feature-card animate-fade-in-up stagger-2" id="feature-personalize">
              <div className="feature-icon">
                <HeartPulse size={28} />
              </div>
              <h3>Personal Suitability</h3>
              <p>Every analysis considers your goals, dietary preferences, allergies, and health context. Your report is truly yours.</p>
            </div>

            <div className="feature-card animate-fade-in-up stagger-3" id="feature-evidence">
              <div className="feature-icon">
                <FlaskConical size={28} />
              </div>
              <h3>Evidence-Based</h3>
              <p>Scientific research, regulatory data, and expert assessments — ranked by evidence strength with full transparency.</p>
            </div>

            <div className="feature-card animate-fade-in-up stagger-4" id="feature-community">
              <div className="feature-icon">
                <Users size={28} />
              </div>
              <h3>People Like You</h3>
              <p>See what users with similar profiles experienced. Not just popular reviews — genuinely relevant experiences.</p>
            </div>

            <div className="feature-card animate-fade-in-up stagger-5" id="feature-confidence">
              <div className="feature-icon">
                <BarChart3 size={28} />
              </div>
              <h3>Confidence Scoring</h3>
              <p>Every recommendation includes a confidence level. We tell you when we're uncertain — never fabricate certainty.</p>
            </div>

            <div className="feature-card animate-fade-in-up stagger-6" id="feature-transparency">
              <div className="feature-icon">
                <Eye size={28} />
              </div>
              <h3>Full Transparency</h3>
              <p>Every claim is traceable. See where information comes from, how strong the evidence is, and what remains uncertain.</p>
            </div>
          </div>
        </div>
      </section>

      {/* How It Works */}
      <section className="how-section" id="how-it-works">
        <div className="container">
          <div className="section-header animate-fade-in-up">
            <span className="section-tag">Process</span>
            <h2>
              From Scan to <span>Understanding</span>
            </h2>
          </div>

          <div className="how-steps">
            <div className="how-step animate-fade-in-up stagger-1">
              <div className="step-number">01</div>
              <div className="step-content">
                <h3>Scan or Search</h3>
                <p>Scan a barcode, snap a photo of the ingredient list, or search by name. We'll identify the product.</p>
              </div>
              <div className="step-icon">
                <Search size={32} />
              </div>
            </div>

            <div className="step-connector" />

            <div className="how-step animate-fade-in-up stagger-2">
              <div className="step-number">02</div>
              <div className="step-content">
                <h3>Analyze Everything</h3>
                <p>Ingredients, nutrition, allergens, scientific evidence, regulatory data, and community experiences — all analyzed.</p>
              </div>
              <div className="step-icon">
                <Brain size={32} />
              </div>
            </div>

            <div className="step-connector" />

            <div className="how-step animate-fade-in-up stagger-3">
              <div className="step-number">03</div>
              <div className="step-content">
                <h3>Personalize For You</h3>
                <p>Your goals, preferences, allergies, and context shape a report that's uniquely relevant to you.</p>
              </div>
              <div className="step-icon">
                <Zap size={32} />
              </div>
            </div>

            <div className="step-connector" />

            <div className="how-step animate-fade-in-up stagger-4">
              <div className="step-number">04</div>
              <div className="step-content">
                <h3>Decide With Confidence</h3>
                <p>Get a personalized suitability score, evidence quality, community insights, and better alternatives.</p>
              </div>
              <div className="step-icon">
                <Shield size={32} />
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* Promise Section */}
      <section className="promise-section">
        <div className="container">
          <div className="promise-card animate-fade-in-up">
            <div className="promise-content">
              <h2>Our Promise</h2>
              <ul className="promise-list">
                <li><CheckCircle2 size={20} /> We never reduce health to a single "good" or "bad" score</li>
                <li><CheckCircle2 size={20} /> We never fabricate data, studies, or user experiences</li>
                <li><CheckCircle2 size={20} /> We always show confidence levels and evidence strength</li>
                <li><CheckCircle2 size={20} /> We always explain where information comes from</li>
                <li><CheckCircle2 size={20} /> We prefer "we don't know" over fabricated certainty</li>
                <li><CheckCircle2 size={20} /> Your health data remains private and under your control</li>
              </ul>
            </div>
          </div>
        </div>
      </section>

      {/* CTA Section */}
      <section className="cta-section" id="cta-section">
        <div className="container">
          <div className="cta-content animate-fade-in-up">
            <h2>Ready to Look Beyond the Label?</h2>
            <p>Join thousands making informed product decisions with personalized intelligence.</p>
            <Link to="/register" className="btn btn-primary btn-lg" id="cta-register">
              Get Started Free
              <ArrowRight size={20} />
            </Link>
          </div>
        </div>
      </section>

      {/* Footer */}
      <footer className="footer" id="main-footer">
        <div className="container">
          <div className="footer-content">
            <div className="footer-brand">
              <div className="navbar-brand">
                <div className="brand-icon">
                  <ScanLine size={20} />
                </div>
                <span className="brand-text">ARIVIO</span>
              </div>
              <p className="footer-tagline">Look Beyond the Label</p>
            </div>
            <div className="footer-meta">
              <p>© {new Date().getFullYear()} ARIVIO. All rights reserved.</p>
              <p className="footer-disclaimer">
                ARIVIO provides decision support, not medical advice. Always consult qualified professionals for health decisions.
              </p>
            </div>
          </div>
        </div>
      </footer>
    </div>
  );
}
