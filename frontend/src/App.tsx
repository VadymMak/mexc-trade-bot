// src/App.tsx
import { useSymbols } from "@/store/symbols";
import { useSSEQuotes } from "@/hooks/useSSEQuotes";

export default function App() {
  // subscribe to whatever cards the user added
  const symbols = useSymbols((s) => s.items.map((it) => it.symbol));
  useSSEQuotes(symbols);
  return null; // routes render elsewhere
}
