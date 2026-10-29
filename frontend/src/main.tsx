import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Route, Routes } from "react-router-dom";
import { PublicFormPage } from "./pages/PublicFormPage";
import "@fontsource-variable/plus-jakarta-sans";
import "./styles.css";
import "./theme.css";

const queryClient = new QueryClient({ defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: true } } });

const isPublic = location.pathname.startsWith("/f/") || location.pathname.startsWith("/r/");

/** Public form addresses load only the form page: no session call, and none of the application's code is sent to a visitor. */
async function start() {
  const root = createRoot(document.getElementById("root")!);
  const page = isPublic ? (
    <Routes>
      <Route path="/f/:token" element={<PublicFormPage />} />
      <Route path="/r/:token" element={<PublicFormPage kind="resume-forms" />} />
    </Routes>
  ) : (
    (() => {
      const { App } = app!;
      return <App />;
    })()
  );
  root.render(
    <StrictMode>
      <QueryClientProvider client={queryClient}>
        <BrowserRouter>{page}</BrowserRouter>
      </QueryClientProvider>
    </StrictMode>,
  );
}
let app: typeof import("./App") | undefined;
(isPublic ? Promise.resolve() : import("./App").then((m) => { app = m; })).then(start);
