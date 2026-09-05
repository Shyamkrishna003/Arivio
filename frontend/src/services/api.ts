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

// Where the backend serves non-API files from (uploaded label photos).
export const API_ORIGIN = API_BASE_URL.replace(/\/api\/v1\/?$/, '');

/**
 * Make a product image URL loadable from the browser.
 *
 * Product images come from two places with different shapes: Open Food Facts
 * gives an absolute URL, while a photo the user took is stored as a path like
 * `/uploads/<hash>.jpg`. A bare path in an <img src> resolves against the
 * page's origin — the Vite dev server on :5173 — not the API on :8000, so the
 * user's own photo would 404 while an imported one worked.
 */
export const resolveImageUrl = (url?: string | null): string | undefined => {
  if (!url) return undefined;
  if (/^(https?:)?\/\//i.test(url) || url.startsWith('data:')) return url;
  return `${API_ORIGIN}${url.startsWith('/') ? '' : '/'}${url}`;
};

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
  // Full result page. Falls back to Open Food Facts when the local catalogue
  // has no convincing match — those arrive as `external_candidates`, which are
  // NOT products yet and must be imported before they can be opened.
  search: (query: string, page = 1, pageSize = 20) =>
    api.get('/products/search', { params: { q: query, page, page_size: pageSize } }),

  // Typeahead. Local only and deliberately cheap — safe to call while typing.
  // `signal` lets a newer keystroke abort the request it superseded.
  suggest: (query: string, signal?: AbortSignal) =>
    api.get('/products/suggest', { params: { q: query }, signal }),

  // Turn an external search candidate into a real product, and return it.
  // Idempotent: importing one we already hold returns the existing product.
  importExternal: (data: { source: string; external_id: string }) =>
    api.post('/products/import', data),

  getById: (id: number) => api.get(`/products/${id}`),

  // Saving is a toggle and both calls are idempotent: saving something already
  // saved, or unsaving something that is not saved, both succeed. That keeps a
  // double click, or a second tab, from surfacing an error for a state the
  // user already has.
  save: (id: number) => api.post(`/products/${id}/save`),
  unsave: (id: number) => api.delete(`/products/${id}/save`),

  scan: (barcode: string) => api.post('/products/scan', null, { params: { barcode } }),

  // Store a photo before submitting the product. Separate from submit() so
  // the submit body stays JSON — a 409 from the duplicate guard then doesn't
  // make the user pick their photo again.
  uploadImage: (file: File) => {
    const form = new FormData();
    form.append('file', file);
    return api.post('/products/images', form, {
      headers: { 'Content-Type': undefined },
    });
  },

  submit: (data: {
    name: string;
    brand?: string;
    barcode?: string;
    category?: string;
    ingredients_text?: string;
    // Content hash from uploadImage(), plus what the photo shows. Only a
    // front-of-pack shot becomes the product's thumbnail.
    image_id?: string | null;
    image_type?: string;
    // Set only after the user has been shown a near-identical existing product
    // and confirmed this really is a different one. A 409 carries an object
    // detail ({message, matches}), not a string — callers must handle that.
    force?: boolean;
  }) => api.post('/products/submit', data),
};

// Label scanning (OCR) API
export const ocrAPI = {
  // Reads a product label from a photo. Writes nothing to the catalogue
  // except a product resolved from a checksum-verified barcode — everything
  // else comes back as a proposal for the user to confirm or correct.
  scanLabel: (file: File) => {
    const form = new FormData();
    form.append('file', file);
    // Content-Type is deliberately unset: the browser has to add the
    // multipart boundary itself, and the client default would override it.
    return api.post('/ocr/label', form, { headers: { 'Content-Type': undefined } });
  },

  // Re-read a pending extraction, so the confirmation screen survives a
  // reload without re-uploading the photo or paying for a second model call.
  getExtraction: (extractionId: string) => api.get(`/ocr/label/${extractionId}`),

  // Create a product from the extraction as the user corrected it.
  confirm: (data: {
    extraction_id: string;
    name: string;
    brand?: string | null;
    category?: string | null;
    serving_size?: string | null;
    barcode?: string | null;
    ingredients_text?: string | null;
    nutrition?: Record<string, number | null>;
    // Set only after the user has been shown a near-identical existing
    // product and said this really is a different one.
    force?: boolean;
  }) => api.post('/ocr/confirm', data),
};

// Health context API
//
// Every call answers for the signed-in user only — there is deliberately no
// endpoint that takes a user id. Uploaded documents are parsed server-side in
// memory and never stored; only the markers the user confirms are kept, and
// those are encrypted at rest.
export const healthAPI = {
  // Documents, the patterns they activate, and any goal pulling against them.
  getContext: () => api.get('/health/context'),

  // Explicit consent (PRD §10). Passing false deletes every stored document —
  // that is what the consent copy promises, so the UI must say so first.
  setConsent: (consent: boolean) => api.put('/health/consent', { consent }),

  upload: (file: File) => {
    const form = new FormData();
    form.append('file', file);
    return api.post('/health/documents', form, {
      headers: { 'Content-Type': undefined },
    });
  },

  getDocument: (id: number) => api.get(`/health/documents/${id}`),

  // Save the markers as the user corrected them and mark the document
  // reviewed. Nothing influences a product score until this happens.
  // `markers` is intentionally structural: the server re-derives every field
  // that matters (which analyte a reading is, and whether it is flagged) from
  // the label, so the client's shape is not the thing being trusted.
  confirm: (id: number, data: {
    title?: string;
    markers: object[];
  }) => api.put(`/health/documents/${id}`, data),

  remove: (id: number) => api.delete(`/health/documents/${id}`),
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
  // preference_type must be one of the canonical keys from getOptions().
  addPreference: (data: { preference_type: string; is_hard_constraint?: boolean }) =>
    api.post('/profile/preferences', data),
  removePreference: (id: number) => api.delete(`/profile/preferences/${id}`),
  getDashboard: () => api.get('/profile/dashboard'),
  recordHistory: (productId: number) => api.post(`/profile/history/${productId}`),
  getSaved: (limit = 50, offset = 0) =>
    api.get('/profile/saved', { params: { limit, offset } }),
  // Served by the API so the dropdowns cannot drift from what it validates.
  getOptions: () => api.get('/profile/options'),
  getPrivacy: () => api.get('/profile/privacy'),
  updatePrivacy: (data: Record<string, boolean>) => api.put('/profile/privacy', data),
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

// Community API
export const communityAPI = {
  // Works signed-out too — the aggregate is public, but the "people like you"
  // split needs a profile to compare against.
  getForProduct: (productId: number, limit = 20) =>
    api.get(`/community/products/${productId}`, { params: { limit } }),
  submitReview: (data: {
    product_id: number;
    usage_duration: string;
    experience_type: string;
    experience_text?: string;
    rating?: number;
    share_context?: boolean;
  }) => api.post('/community/reviews', data),
  vote: (reviewId: number, isHelpful: boolean) =>
    api.post(`/community/reviews/${reviewId}/vote`, { is_helpful: isHelpful }),
  flag: (reviewId: number, reason?: string) =>
    api.post(`/community/reviews/${reviewId}/flag`, { reason }),
  removeReview: (reviewId: number) =>
    api.delete(`/community/reviews/${reviewId}`),
};
