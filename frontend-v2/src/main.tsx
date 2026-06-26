import React from 'react';
import ReactDOM from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
import { GoogleOAuthProvider } from '@react-oauth/google';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import App from './App';
import { AuthProvider } from './auth/AuthProvider';
import './styles/globals.css';

// React Query cache. Defaults tuned for V-Studio:
//   • staleTime 30s  — most lists are stable for at least that long; avoids
//                       refetching folders/books on every nav.
//   • gcTime 5min    — keep cached pages around so back-button feels instant.
//   • refetchOnWindowFocus disabled — too aggressive for this workflow.
const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30 * 1000,
      gcTime: 5 * 60 * 1000,
      refetchOnWindowFocus: false,
      retry: 1,
    },
  },
});

// Real client ID in production. A placeholder in dev-stub mode so the
// useGoogleLogin hook still has a context to bind to — the placeholder is
// never actually used because the Login page short-circuits to the dev
// stub when VITE_GOOGLE_CLIENT_ID is absent.
const GOOGLE_CLIENT_ID =
  (import.meta.env.VITE_GOOGLE_CLIENT_ID ?? '').trim() || 'dev-stub.invalid';

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <GoogleOAuthProvider clientId={GOOGLE_CLIENT_ID}>
        <BrowserRouter>
          <AuthProvider>
            <App />
          </AuthProvider>
        </BrowserRouter>
      </GoogleOAuthProvider>
    </QueryClientProvider>
  </React.StrictMode>
);
