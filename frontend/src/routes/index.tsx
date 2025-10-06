import { createBrowserRouter, Navigate } from "react-router-dom";
import Dashboard from "@/pages/Dashboard";
import LiquidityScanner from "@/pages/LiquidityScanner";
import TradingBoard from "@/pages/TradingBoard";

const basename = import.meta.env.BASE_URL || "/";

const router = createBrowserRouter(
  [
    // теперь root = Trade
    { path: "/", element: <TradingBoard /> },

    // Dashboard вынесен на отдельный путь
    { path: "/dashboard", element: <Dashboard /> },

    { path: "/scanner", element: <LiquidityScanner /> },
    { path: "/trade", element: <Navigate to="/" replace /> }, // редирект для старого пути

    // 404
    {
      path: "*",
      element: (
        <div className="p-6 text-center space-y-4">
          <h1 className="text-2xl font-semibold">404 — Not Found</h1>
          <p className="text-zinc-400">Страница не найдена.</p>
          <a
            href="/"
            className="inline-block px-4 py-2 rounded-lg bg-indigo-700 hover:bg-indigo-600"
          >
            На главную
          </a>
        </div>
      ),
    },
  ],
  { basename }
);

export default router;
