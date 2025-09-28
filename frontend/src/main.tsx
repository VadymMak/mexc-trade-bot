// src/index.tsx
import React from "react";
import { createRoot } from "react-dom/client";
import { RouterProvider } from "react-router-dom";
import router from "@/routes";
import "@/styles/index.css";
import Toaster from "@/components/common/Toaster";
import StrategySettingsHost from "@/components/settings/StrategySettingsHost";
import SSEBridge from "@/bridges/SSEBridge";
import BootBridge from "@/bridges/BootBridge"; // ← add

createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <RouterProvider router={router} />
    <BootBridge />              
    <SSEBridge />              
    <StrategySettingsHost />   
    <Toaster />
  </React.StrictMode>
);
