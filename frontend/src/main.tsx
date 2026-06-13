import React from "react";
import ReactDOM from "react-dom/client";
import { App } from "./App";
import { AuthGate } from "./components/AuthGate";
import { installAuthFetch } from "./lib/auth";
import "./index.css";

// Attach the bearer token to every same-origin request (incl. the SSE consumer) once, at startup.
installAuthFetch();

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <AuthGate>{(auth) => <App auth={auth} />}</AuthGate>
  </React.StrictMode>,
);
