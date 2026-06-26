import { Sidebar } from "./components/Sidebar";
import { MockBanner } from "./components/MockBanner";
import { LibraryPage } from "./pages/LibraryPage";
import { UploadPage } from "./pages/UploadPage";
import { SchemaPage } from "./pages/SchemaPage";
import { ReaderPage } from "./pages/ReaderPage";
import { RegenPage } from "./pages/RegenPage";
import { SettingsPage } from "./pages/SettingsPage";
import { QuestionsPage } from "./pages/QuestionsPage";
import { FiguresPage } from "./pages/FiguresPage";
import { FinalComposerPage } from "./pages/FinalComposerPage";
import { FinalMergePage } from "./pages/FinalMergePage";
import { FinalPreviewPage } from "./pages/FinalPreviewPage";
import { useUI } from "./stores/ui";

export default function App() {
  const view = useUI((s) => s.view);
  return (
    <div className="app">
      <Sidebar />
      <main className="main">
        <MockBanner />
        {view === "library" && <LibraryPage />}
        {view === "upload" && <UploadPage />}
        {view === "schema" && <SchemaPage />}
        {view === "reader" && <ReaderPage />}
        {view === "regen" && <RegenPage />}
        {view === "settings" && <SettingsPage />}
        {view === "questions" && <QuestionsPage />}
        {view === "images" && <FiguresPage />}
        {view === "final" && <FinalMergePage />}
        {view === "compose" && <FinalComposerPage />}
        {view === "preview" && <FinalPreviewPage />}
      </main>
    </div>
  );
}
