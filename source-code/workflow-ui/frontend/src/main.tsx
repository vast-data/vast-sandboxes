import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import "./styles/base.css";
import "./styles/controls.css";
import "./styles/auth.css";
import "./styles/shell.css";
import "./styles/detail.css";
import "./styles/admin.css";
import "./styles/charts.css";
import "./styles/summary.css";
import "./styles/panels.css";
import "./styles/dashboard.css";
import "./styles/features.css";
import "./styles/turbo.css";
import { ToastProvider } from "./toast";

ReactDOM.createRoot(document.getElementById("root") as HTMLElement).render(
  <React.StrictMode>
    <ToastProvider>
      <App />
    </ToastProvider>
  </React.StrictMode>,
);
