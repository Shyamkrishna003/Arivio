import axios from 'axios';
import type { AxiosRequestConfig } from 'axios';
import { store } from '../store';
import { logout, setCredentials } from '../store/slices/authSlice';

const API_BASE_URL = import.meta.env.VITE_API_URL || `http://${window.location.hostname}:8000/api/v1`;

const api = axios.create({
  baseURL: API_BASE_URL,
  headers: {
    'Content-Type': 'application/json',
  },
});

// Request interceptor: attach JWT token
api.interceptors.request.use(
  (config) => {
    const token = store.getState().auth.accessToken;
    if (token) {
      config.headers.Authorization = `Bearer ${token}`;
    }
    return config;
  },
  (error) => Promise.reject(error)
);

// Response interceptor: refresh the access token on 401 instead of logging out.
//
// The access token lives 30 minutes but the refresh token lives 7 days, so a
// bare logout on 401 was ending sessions half an hour in. On the first 401 we
// exchange the refresh token and replay the original request; concurrent 401s
// queue behind that single refresh rather than each firing their own.

type RetriableConfig = AxiosRequestConfig & { _retry?: boolean };

let isRefreshing = false;
let waiters: Array<{
  resolve: (token: string) => void;
  reject: (error: unknown) => void;
}> = [];

const flushWaiters = (token: string | null, error?: unknown) => {
  waiters.forEach((w) => (token ? w.resolve(token) : w.reject(error)));
  waiters = [];
};

const endSession = () => {
  store.dispatch(logout());
  // Avoid a redirect loop if we are already on the landing page.
  if (window.location.pathname !== '/') {
    window.location.href = '/';
  }
};

// Endpoints where a 401 is a legitimate answer (bad credentials, dead refresh
// token) rather than an expired access token to retry.
const isAuthEndpoint = (url?: string) =>
  !!url && (url.includes('/auth/login') || url.includes('/auth/register') || url.includes('/auth/refresh'));

api.interceptors.response.use(
  (response) => response,
  async (error) => {
    const original = error.config as RetriableConfig | undefined;

    if (error.response?.status !== 401 || !original || isAuthEndpoint(original.url)) {
      // A dead refresh token means the session really is over.
      if (error.response?.status === 401 && original?.url?.includes('/auth/refresh')) {
        endSession();
      }
      return Promise.reject(error);
    }

    if (original._retry) {
      endSession();
      return Promise.reject(error);
    }

    const refreshToken = store.getState().auth.refreshToken;
    if (!refreshToken) {
      endSession();
      return Promise.reject(error);
    }

    if (isRefreshing) {
      return new Promise<string>((resolve, reject) => {
        waiters.push({ resolve, reject });
      }).then((token) => {
        original._retry = true;
        original.headers = { ...original.headers, Authorization: `Bearer ${token}` };
        return api(original);
      });
    }

    original._retry = true;
    isRefreshing = true;
    try {
      // Plain axios, not `api` — going through the instance would recurse
      // straight back into this interceptor.
      const { data } = await axios.post(`${API_BASE_URL}/auth/refresh`, {
        refresh_token: refreshToken,
      });
      store.dispatch(setCredentials(data));
      flushWaiters(data.access_token);
      original.headers = { ...original.headers, Authorization: `Bearer ${data.access_token}` };
      return api(original);
    } catch (refreshError) {
      flushWaiters(null, refreshError);
      endSession();
      return Promise.reject(refreshError);
    } finally {
      isRefreshing = false;
    }
  }
);

export default api;

// Auth API
export const authAPI = {
  register: (data: { email: string; username: string; password: string; full_name?: string }) =>
    api.post('/auth/register', data),

  login: (data: { email: string; password: string }) =>
    api.post('/auth/login', data),

  me: () => api.get('/auth/me'),

  refresh: (refreshToken: string) =>
    api.post('/auth/refresh', { refresh_token: refreshToken }),
};

// Products API
export const productsAPI = {
  search: (query: string, page = 1, pageSize = 20) =>
    api.get('/products/search', { params: { q: query, page, page_size: pageSize } }),

  getById: (id: number) => api.get(`/products/${id}`),

  scan: (barcode: string) => api.post('/products/scan', null, { params: { barcode } }),

  submit: (data: {
    name: string;
    brand?: string;
    barcode?: string;
    category?: string;
    ingredients_text?: string;
  }) => api.post('/products/submit', data),
};

// Profile API
export const profileAPI = {
  get: () => api.get('/profile'),
  update: (data: Record<string, unknown>) => api.put('/profile', data),
  addGoal: (data: { goal_type: string; priority?: number }) => api.post('/profile/goals', data),
  removeGoal: (id: number) => api.delete(`/profile/goals/${id}`),
  addAllergy: (data: { allergen: string; allergy_type: string; severity?: string }) =>
    api.post('/profile/allergies', data),
  removeAllergy: (id: number) => api.delete(`/profile/allergies/${id}`),
  knownAllergens: () => api.get<string[]>('/profile/allergens/known'),
  addPreference: (data: { preference_type: string; is_hard_constraint?: boolean }) =>
    api.post('/profile/preferences', data),
  removePreference: (id: number) => api.delete(`/profile/preferences/${id}`),
  getDashboard: () => api.get('/profile/dashboard'),
  recordHistory: (productId: number) => api.post(`/profile/history/${productId}`),
};

// Personalization API
export const personalizationAPI = {
  getSuitability: (productId: number) =>
    api.get(`/personalization/suitability/${productId}`),
  getAlternatives: (productId: number, limit = 5) =>
    api.get(`/personalization/alternatives/${productId}`, { params: { limit } }),
};

// AI Report API
export const aiAPI = {
  getReport: (productId: number) =>
    api.get(`/ai/report/${productId}`),
  submitFeedback: (data: {
    product_id: number;
    rating: number;
    feedback_type?: string;
    comment?: string;
  }) => api.post('/ai/feedback', data),
  getFeedback: (productId: number) =>
    api.get(`/ai/feedback/${productId}`),
};
