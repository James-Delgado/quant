import { Navigate, Route, Routes } from "react-router-dom";
import { AppShell } from "@/components/layout/AppShell";
import { Placeholder } from "@/pages/Placeholder";
import { NAV_ITEMS, DEFAULT_PATH } from "@/nav";

/**
 * Route table. The shell wraps every panel; each nav slug renders a panel body.
 * In E1-M2 the bodies are placeholders — M3/M4 swap individual routes for real
 * page components without touching the shell.
 */
export function App() {
  return (
    <Routes>
      <Route element={<AppShell />}>
        <Route index element={<Navigate to={`/${DEFAULT_PATH}`} replace />} />
        {NAV_ITEMS.map((item) => (
          <Route
            key={item.path}
            path={item.path}
            element={<Placeholder slug={item.path} />}
          />
        ))}
        <Route path="*" element={<Navigate to={`/${DEFAULT_PATH}`} replace />} />
      </Route>
    </Routes>
  );
}
