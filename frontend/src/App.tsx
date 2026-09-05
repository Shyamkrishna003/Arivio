import { useEffect } from 'react';
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { Provider, useSelector, useDispatch } from 'react-redux';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { store } from './store/index.ts';
import type { RootState } from './store/index.ts';
import { setUser } from './store/slices/authSlice';
import { authAPI } from './services/api';
import Navbar from './components/layout/Navbar';
import Landing from './pages/landing/Landing';
import Login from './pages/auth/Login';
import Register from './pages/auth/Register';
import Dashboard from './pages/dashboard/Dashboard';
import Scan from './pages/scan/Scan';
import LabelConfirm from './pages/scan/LabelConfirm';
import ProductSearch from './pages/product/ProductSearch';
import ProductSubmit from './pages/product/ProductSubmit';
import ProductDetail from './pages/product/ProductDetail';
import Profile from './pages/profile/Profile';

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      refetchOnWindowFocus: false,
      retry: 1,
      staleTime: 5 * 60 * 1000,
    },
  },
});

// `isAuthenticated` is restored from the stored token on boot, but the user
// object is not — it only lives in memory. Without this, reloading any page
// other than the dashboard (which fetches it itself) left the account details
// blank even though the session was perfectly valid.
function useHydrateUser() {
  const { isAuthenticated, user } = useSelector((state: RootState) => state.auth);
  const dispatch = useDispatch();

  useEffect(() => {
    if (!isAuthenticated || user) return;
    authAPI.me()
      .then((res) => dispatch(setUser(res.data)))
      .catch(() => {
        // A dead token is handled by the response interceptor; nothing to do.
      });
  }, [isAuthenticated, user, dispatch]);
}

function ProtectedRoute({ children }: { children: React.ReactNode }) {
  const { isAuthenticated } = useSelector((state: RootState) => state.auth);
  if (!isAuthenticated) {
    return <Navigate to="/" replace />;
  }
  return <>{children}</>;
}

function PublicRoute({ children }: { children: React.ReactNode }) {
  const { isAuthenticated } = useSelector((state: RootState) => state.auth);
  if (isAuthenticated) {
    return <Navigate to="/dashboard" replace />;
  }
  return <>{children}</>;
}

function AppRoutes() {
  useHydrateUser();
  return (
    <BrowserRouter>
      <Navbar />
      <Routes>
        {/* Public Routes */}
        <Route path="/" element={<Landing />} />
        <Route
          path="/login"
          element={
            <PublicRoute>
              <Login />
            </PublicRoute>
          }
        />
        <Route
          path="/register"
          element={
            <PublicRoute>
              <Register />
            </PublicRoute>
          }
        />

        {/* Protected Routes */}
        <Route
          path="/dashboard"
          element={
            <ProtectedRoute>
              <Dashboard />
            </ProtectedRoute>
          }
        />
        <Route
          path="/scan"
          element={
            <ProtectedRoute>
              <Scan />
            </ProtectedRoute>
          }
        />
        {/* Where a photographed label is corrected before anything is
            analyzed (PRD §14). Keyed by extraction id so a reload survives. */}
        <Route
          path="/scan/confirm/:extractionId"
          element={
            <ProtectedRoute>
              <LabelConfirm />
            </ProtectedRoute>
          }
        />
        <Route
          path="/products"
          element={
            <ProtectedRoute>
              <ProductSearch />
            </ProtectedRoute>
          }
        />
        {/* Declared before the :id route so "submit" is never read as an id.
            Both the scanner (unknown barcode) and search (no results) link
            here. */}
        <Route
          path="/products/submit"
          element={
            <ProtectedRoute>
              <ProductSubmit />
            </ProtectedRoute>
          }
        />
        <Route
          path="/products/:id"
          element={
            <ProtectedRoute>
              <ProductDetail />
            </ProtectedRoute>
          }
        />
        <Route
          path="/profile"
          element={
            <ProtectedRoute>
              <Profile />
            </ProtectedRoute>
          }
        />

        {/* Catch all */}
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </BrowserRouter>
  );
}

export default function App() {
  return (
    <Provider store={store}>
      <QueryClientProvider client={queryClient}>
        <AppRoutes />
      </QueryClientProvider>
    </Provider>
  );
}
