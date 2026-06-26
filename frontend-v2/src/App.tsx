import { Navigate, Route, Routes } from 'react-router-dom';

import AppShell from './components/AppShell';
import { ToastProvider } from './components/Toast';
import RequireAuth from './auth/RequireAuth';
import LoginPage from './pages/LoginPage';
import LibraryPage from './pages/LibraryPage';
import BookPage from './pages/BookPage';
import ChapterPage from './pages/ChapterPage';
import UploadPage from './pages/UploadPage';
import TemplatesPage from './pages/TemplatesPage';
import SettingsPage from './pages/SettingsPage';
import FolderPage from './pages/FolderPage';
import DevExtractPage from './pages/DevExtractPage';
import BookExtractPage from './pages/BookExtractPage';
import ReviewPage from './pages/ReviewPage';
import ComposerPage from './pages/ComposerPage';
import PreviewPage from './pages/PreviewPage';
import RegenConfigPage from './pages/RegenConfigPage';
import RegenProgressPage from './pages/RegenProgressPage';
import RegenReviewPage from './pages/RegenReviewPage';

export default function App() {
  return (
    <ToastProvider>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route
          element={
            <RequireAuth>
              <AppShell />
            </RequireAuth>
          }
        >
          <Route index element={<Navigate to="/library" replace />} />
          <Route path="library"               element={<LibraryPage />} />
          <Route path="upload"                element={<UploadPage />} />
          <Route path="folders/:folderId"     element={<FolderPage />} />
          <Route path="books/:bookId"                       element={<BookPage />} />
          <Route path="books/:bookId/extract"               element={<BookExtractPage />} />
          <Route path="books/:bookId/review"                element={<ReviewPage />} />
          <Route path="books/:bookId/regenerate"            element={<RegenConfigPage />} />
          <Route path="books/:bookId/regenerate/progress"   element={<RegenProgressPage />} />
          <Route path="books/:bookId/regen-review"          element={<RegenReviewPage />} />
          <Route path="books/:bookId/compose"               element={<ComposerPage />} />
          <Route path="books/:bookId/preview"               element={<PreviewPage />} />
          <Route path="books/:bookId/chapters/:chapterId"   element={<ChapterPage />} />
          <Route path="templates" element={<TemplatesPage />} />
          <Route path="settings"  element={<SettingsPage />} />
          <Route path="dev/extract" element={<DevExtractPage />} />
        </Route>
        <Route path="*" element={<Navigate to="/library" replace />} />
      </Routes>
    </ToastProvider>
  );
}
