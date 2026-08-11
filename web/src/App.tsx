import { lazy, Suspense } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import { Shell } from "./components/Shell";
import { Loading } from "./components/States";
import { ArchivePage } from "./pages/ArchivePage";
import { BriefPage } from "./pages/BriefPage";
import { LibraryPage } from "./pages/LibraryPage";
import { MemoryPage } from "./pages/MemoryPage";
import { SettingsPage } from "./pages/SettingsPage";
import { TasksPage } from "./pages/TasksPage";

const GraphPage = lazy(() => import("./pages/GraphPage").then((module) => ({ default: module.GraphPage })));
const ReaderPage = lazy(() => import("./pages/ReaderPage").then((module) => ({ default: module.ReaderPage })));

export default function App() {
  return (
    <Routes>
      <Route element={<Shell />}>
        <Route path="/" element={<BriefPage />} />
        <Route path="/briefs" element={<ArchivePage />} />
        <Route path="/briefs/:briefId" element={<BriefPage />} />
        <Route path="/graph" element={<Suspense fallback={<Loading label="正在加载图谱" />}><GraphPage /></Suspense>} />
        <Route path="/graph/:nodeId" element={<Suspense fallback={<Loading label="正在加载图谱" />}><GraphPage /></Suspense>} />
        <Route path="/library" element={<LibraryPage />} />
        <Route path="/paper/:paperId" element={<Suspense fallback={<Loading label="正在加载阅读器" />}><ReaderPage /></Suspense>} />
        <Route path="/memory" element={<MemoryPage />} />
        <Route path="/tasks" element={<TasksPage />} />
        <Route path="/settings" element={<SettingsPage />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}
