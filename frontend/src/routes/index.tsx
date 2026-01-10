import { createBrowserRouter, Navigate } from "react-router-dom";
import LiquidityScanner from "@/pages/LiquidityScanner";
import TradingBoard from "@/pages/TradingBoard";
import TradeLog from "@/pages/TradeLog";
import Settings from "@/pages/Settings";

const basename = import.meta.env.BASE_URL || "/";

const router = createBrowserRouter(
  [
    // Root = TradingBoard
    { path: "/", element: <TradingBoard /> },
    
    // Main pages
    { path: "/scanner", element: <LiquidityScanner /> },
    { path: "/trades", element: <TradeLog /> },
    { path: "/settings", element: <Settings /> },
    
    // Redirects for old paths
    { path: "/trade", element: <Navigate to="/" replace /> },
    { path: "/dashboard", element: <Navigate to="/" replace /> },
    
    // 404
    {
      path: "*",
      element: (
        <div className="p-6 text-center space-y-4">
          <h1 className="text-2xl font-semibold">404 - Not Found</h1>
          <p className="text-zinc-400">Page not found</p>
          
            <a href="/"
            className="inline-block px-4 py-2 rounded-lg bg-indigo-700 hover:bg-indigo-600"
          >
            Go Home
          </a>
        </div>
      ),
    },
  ],
  { basename }
);

export default router;